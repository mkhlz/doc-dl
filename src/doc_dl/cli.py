from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from doc_dl import __version__
from doc_dl.doctor import doctor_payload, run_doctor
from doc_dl.engine import DownloadEngine
from doc_dl.errors import DocDlError
from doc_dl.events import EventSink
from doc_dl.models import DownloadRequest
from doc_dl.providers.registry import ProviderRegistry
from doc_dl.session import SessionManager

COMMANDS = {"download", "login", "logout", "providers", "doctor", "version"}


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
        print(json.dumps(payload, ensure_ascii=False))
    elif not args.quiet:
        for check in checks:
            label = "OK" if check.ok else "MISSING"
            print(f"[{label}] {check.name}: {check.detail}")
    return 0 if payload["ok"] else 50


def run(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    command = arguments[0].casefold() if arguments else ""
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


def main(argv: Sequence[str] | None = None) -> int:
    code = run(argv)
    if argv is None:
        raise SystemExit(code)
    return code
