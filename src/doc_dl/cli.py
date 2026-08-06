from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from doc_dl import __version__
from doc_dl.config import StatePaths
from doc_dl.doctor import doctor_payload, run_doctor
from doc_dl.engine import DownloadEngine
from doc_dl.errors import DocDlError
from doc_dl.events import EventSink, safe_print
from doc_dl.models import DownloadRequest
from doc_dl.providers.registry import ProviderRegistry
from doc_dl.runtime import (
    chromium_executable_path,
    configure_browsers_path,
    effective_browsers_path,
    install_chromium,
    is_chromium_installed,
    uninstall_chromium,
)
from doc_dl.session import SessionManager

COMMANDS = {
    "download",
    "login",
    "logout",
    "providers",
    "doctor",
    "install-browser",
    "uninstall-browser",
    "clean",
    "version",
}


class DocDlArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise DocDlError("invalid_arguments", message)


def _add_output_modes(parser: argparse.ArgumentParser) -> None:
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--json", action="store_true", help="Emit newline-delimited JSON events")
    modes.add_argument("--quiet", action="store_true", help="Print only errors and the final path")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print strategy diagnostics")


def _download_parser() -> DocDlArgumentParser:
    parser = DocDlArgumentParser(
        prog="doc-dl",
        description="Download and verify documents from modern websites.",
    )
    parser.add_argument("url", nargs="?", help="Document or landing-page URL")
    parser.add_argument("-Url", dest="url_option", help="PowerShell-style URL compatibility form")
    parser.add_argument("-o", "--output", type=Path, help="Output file or directory")
    parser.add_argument(
        "--filename",
        dest="filename_template",
        help="Filename template using {title}, {ext}, {provider}, or {filename}",
    )
    parser.add_argument(
        "--original-only",
        action="store_true",
        help="Reject reconstructed or printed output",
    )
    parser.add_argument(
        "--render",
        action="store_true",
        help="Explicitly permit rendered PDF fallback; enabled by default",
    )
    parser.add_argument("--provider", dest="forced_provider", help="Force a provider adapter")
    parser.add_argument("--profile", default="default", help="Isolated browser profile name")
    parser.add_argument("--no-browser", action="store_true", help="Disable browser escalation")
    parser.add_argument("--no-resume", action="store_true", help="Disable partial-transfer resume")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing output")
    parser.add_argument("--timeout", default="180s", help="Total timeout, such as 90s, 5m, or 1h")
    parser.add_argument("--retries", type=int, default=3, help="Transient retry count")
    parser.add_argument(
        "--write-metadata",
        action="store_true",
        help="Write a redacted .doc-dl.json provenance sidecar",
    )
    parser.add_argument("--version", action="version", version=f"doc-dl {__version__}")
    _add_output_modes(parser)
    return parser


def _login_parser(command: str) -> DocDlArgumentParser:
    description = (
        "Create an isolated provider login profile"
        if command == "login"
        else ("Delete an isolated provider login profile")
    )
    parser = DocDlArgumentParser(prog=f"doc-dl {command}", description=description)
    parser.add_argument("provider", help="Provider name")
    parser.add_argument("--profile", default="default", help="Isolated profile name")
    if command == "logout":
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Delete without an interactive prompt",
        )
    _add_output_modes(parser)
    return parser


def _simple_parser(command: str) -> DocDlArgumentParser:
    parser = DocDlArgumentParser(prog=f"doc-dl {command}")
    _add_output_modes(parser)
    return parser


def _install_browser_parser() -> DocDlArgumentParser:
    parser = DocDlArgumentParser(
        prog="doc-dl install-browser",
        description="Download and install the Chromium browser runtime",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reinstall even if Chromium is already present",
    )
    _add_output_modes(parser)
    return parser


def _uninstall_browser_parser() -> DocDlArgumentParser:
    parser = DocDlArgumentParser(
        prog="doc-dl uninstall-browser",
        description="Remove the Chromium browser runtime downloaded by doc-dl",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Delete without an interactive prompt",
    )
    _add_output_modes(parser)
    return parser


def _clean_parser() -> DocDlArgumentParser:
    parser = DocDlArgumentParser(
        prog="doc-dl clean",
        description=(
            "Remove orphaned .doc-dl-*.part partial-download files left behind "
            "by interrupted or failed downloads"
        ),
    )
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=Path.cwd(),
        help="Directory to scan (defaults to the current directory)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Delete without an interactive prompt",
    )
    _add_output_modes(parser)
    return parser


def parse_duration(value: str) -> float:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*", value, flags=re.IGNORECASE)
    if not match:
        raise DocDlError(
            "invalid_arguments",
            f"Invalid timeout value: {value!r}",
            detail="Use seconds or a suffix such as 90s, 5m, or 1h.",
        )
    amount = float(match.group(1))
    multiplier = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0}[match.group(2).casefold()]
    seconds = amount * multiplier
    if seconds <= 0 or seconds > 24 * 3600:
        raise DocDlError("invalid_arguments", "Timeout must be between 0 and 24 hours")
    return seconds


def _sink_from_args(args: argparse.Namespace) -> EventSink:
    return EventSink(
        json_mode=bool(getattr(args, "json", False)),
        quiet=bool(getattr(args, "quiet", False)),
        verbose=bool(getattr(args, "verbose", False)),
    )


def _resolve_url(args: argparse.Namespace) -> str:
    positional = args.url
    option = args.url_option
    if positional and option and positional != option:
        raise DocDlError(
            "invalid_arguments",
            "The positional URL and -Url option specify different values",
        )
    url = positional or option
    if not url:
        raise DocDlError("invalid_arguments", "A document URL is required")
    return str(url)


def _run_download(argv: Sequence[str]) -> int:
    args = _download_parser().parse_args(list(argv))
    sink = _sink_from_args(args)
    if args.retries < 0 or args.retries > 20:
        raise DocDlError("invalid_arguments", "Retries must be between 0 and 20")
    request = DownloadRequest(
        url=_resolve_url(args),
        output=args.output,
        filename_template=args.filename_template,
        original_only=args.original_only,
        allow_render=not args.original_only,
        forced_provider=args.forced_provider,
        profile=args.profile,
        browser_enabled=not args.no_browser,
        resume=not args.no_resume,
        overwrite=args.overwrite,
        timeout_seconds=parse_duration(args.timeout),
        retries=args.retries,
        write_metadata=args.write_metadata,
    )
    DownloadEngine(sink).download(request)
    return 0


def _run_login(command: str, argv: Sequence[str]) -> int:
    args = _login_parser(command).parse_args(list(argv))
    sink = _sink_from_args(args)
    registry = ProviderRegistry()
    provider = registry.get(args.provider)
    manager = SessionManager(sink)
    if command == "login":
        manager.login(provider, args.profile)
    else:
        removed = manager.logout(provider, args.profile, confirmed=args.yes)
        if not removed and not args.quiet:
            print("No profile was deleted.")
    return 0


def _run_providers(argv: Sequence[str]) -> int:
    args = _simple_parser("providers").parse_args(list(argv))
    providers = ProviderRegistry().all()
    payload = [
        {
            "name": provider.name,
            "authentication": provider.supports_authentication,
            "render": provider.supports_render,
        }
        for provider in providers
    ]
    if args.json:
        print(json.dumps({"event": "providers", "version": 1, "providers": payload}))
    elif not args.quiet:
        for item in payload:
            capabilities = ["original"]
            if item["authentication"]:
                capabilities.append("authentication")
            if item["render"]:
                capabilities.append("render")
            print(f"{item['name']}: {', '.join(capabilities)}")
    return 0


def _run_doctor(argv: Sequence[str]) -> int:
    args = _simple_parser("doctor").parse_args(list(argv))
    checks = run_doctor()
    payload = doctor_payload(checks)
    if args.json:
        safe_print(json.dumps(payload, ensure_ascii=False), file=sys.stdout)
    elif not args.quiet:
        for check in checks:
            label = "OK" if check.ok else "MISSING"
            safe_print(f"[{label}] {check.name}: {check.detail}", file=sys.stdout)
    return 0 if payload["ok"] else 50


def _run_install_browser(argv: Sequence[str]) -> int:
    args = _install_browser_parser().parse_args(list(argv))
    sink = _sink_from_args(args)
    if not args.force and is_chromium_installed():
        executable = chromium_executable_path()
        sink.emit(
            "complete",
            message=f"Chromium is already installed at {executable}",
            path=str(executable),
        )
        return 0
    executable = install_chromium(sink)
    sink.emit(
        "complete",
        message=f"Chromium installed at {executable}",
        path=str(executable),
    )
    return 0


def _run_uninstall_browser(argv: Sequence[str]) -> int:
    args = _uninstall_browser_parser().parse_args(list(argv))
    sink = _sink_from_args(args)
    state = StatePaths.discover()
    target = effective_browsers_path(state)
    if not args.yes and target.exists():
        answer = input(f"Delete the Chromium browser runtime at {target}? [y/N] ")
        if answer.strip().casefold() not in {"y", "yes"}:
            if not args.quiet:
                print("No browser data was deleted.")
            return 0
    removed = uninstall_chromium(state)
    if removed:
        sink.emit(
            "complete",
            message=f"Removed the Chromium browser runtime from {target}",
            path=str(target),
        )
    elif not args.quiet:
        print("No installed browser runtime was found.")
    return 0


def _orphaned_partial_files(directory: Path) -> list[Path]:
    found = set(directory.glob(".doc-dl-*.part")) | set(directory.glob(".doc-dl-*.part.json"))
    return sorted(found)


def _run_clean(argv: Sequence[str]) -> int:
    args = _clean_parser().parse_args(list(argv))
    sink = _sink_from_args(args)
    directory = args.directory.expanduser().resolve()
    orphans = _orphaned_partial_files(directory)
    if not orphans:
        if not args.quiet:
            print(f"No leftover .doc-dl-*.part files were found in {directory}.")
        return 0

    total_size = sum(path.stat().st_size for path in orphans if path.exists())
    if not args.yes:
        if not args.quiet:
            print(f"Found {len(orphans)} leftover file(s) in {directory} ({total_size} bytes):")
            for path in orphans:
                print(f"  {path.name}")
        answer = input("Delete these files? [y/N] ")
        if answer.strip().casefold() not in {"y", "yes"}:
            if not args.quiet:
                print("No files were deleted.")
            return 0

    removed = 0
    for path in orphans:
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    sink.emit(
        "complete",
        message=f"Removed {removed} leftover file(s) from {directory}",
        path=str(directory),
    )
    return 0


def _print_bare_invocation_help() -> None:
    safe_print(_download_parser().format_help().rstrip(), file=sys.stdout)
    safe_print("", file=sys.stdout)
    safe_print("Quick examples:", file=sys.stdout)
    safe_print('  doc-dl "https://example.com/document.pdf"', file=sys.stdout)
    safe_print('  doc-dl -Url "https://example.com/document.pdf"', file=sys.stdout)
    safe_print("  doc-dl doctor", file=sys.stdout)


def run(argv: Sequence[str] | None = None) -> int:
    configure_browsers_path()
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        _print_bare_invocation_help()
        return 0
    command = arguments[0].casefold()
    command_args = arguments[1:] if command in COMMANDS else arguments
    sink: EventSink | None = None
    try:
        if command == "download":
            return _run_download(command_args)
        if command in {"login", "logout"}:
            return _run_login(command, command_args)
        if command == "providers":
            return _run_providers(command_args)
        if command == "doctor":
            return _run_doctor(command_args)
        if command == "install-browser":
            return _run_install_browser(command_args)
        if command == "uninstall-browser":
            return _run_uninstall_browser(command_args)
        if command == "clean":
            return _run_clean(command_args)
        if command == "version":
            print(f"doc-dl {__version__}")
            return 0
        return _run_download(arguments)
    except DocDlError as exc:
        json_mode = "--json" in arguments
        quiet = "--quiet" in arguments
        verbose = "--verbose" in arguments or "-v" in arguments
        sink = EventSink(json_mode=json_mode, quiet=quiet, verbose=verbose)
        sink.emit(
            "error",
            error=exc.identifier,
            message=exc.message,
            detail=exc.detail,
            exit_code=exc.exit_code,
            retryable=exc.retryable,
        )
        return exc.exit_code
    except KeyboardInterrupt:
        sink = sink or EventSink()
        sink.emit("error", error="interrupted", message="Operation interrupted", exit_code=130)
        return 130
    except Exception as exc:
        json_mode = "--json" in arguments
        quiet = "--quiet" in arguments
        verbose = "--verbose" in arguments or "-v" in arguments
        sink = sink or EventSink(json_mode=json_mode, quiet=quiet, verbose=verbose)
        sink.emit(
            "error",
            error="internal_error",
            message="doc-dl hit an unexpected internal error",
            detail=str(exc),
            exit_code=99,
            retryable=False,
        )
        return 99


def main(argv: Sequence[str] | None = None) -> int:
    code = run(argv)
    if argv is None:
        raise SystemExit(code)
    return code
