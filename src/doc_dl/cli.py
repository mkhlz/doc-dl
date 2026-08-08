from __future__ import annotations

import argparse
import io
import itertools
import json
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from doc_dl import __version__
from doc_dl.archive import PageArchiver
from doc_dl.config import StatePaths
from doc_dl.doctor import doctor_payload, run_doctor
from doc_dl.engine import DownloadEngine
from doc_dl.errors import DocDlError
from doc_dl.events import EventSink, safe_print
from doc_dl.filenames import resolve_output_path
from doc_dl.models import ArchiveRequest, DownloadRequest
from doc_dl.pdftools import extract_pdf_pages
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
from doc_dl.ui import BOLD, DIM, GREEN, RED, YELLOW, enable_utf8_output, format_bytes, render_banner

RELEASE_NAME = "Alexandria"

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
    "archive",
    "extract-pages",
}


class DocDlArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise DocDlError("invalid_arguments", message)


def _add_output_modes(parser: argparse.ArgumentParser) -> None:
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--json", action="store_true", help="Emit newline-delimited JSON events")
    modes.add_argument("--quiet", action="store_true", help="Print only errors and the final path")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print strategy diagnostics")


def _add_batch_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--batch",
        type=Path,
        help="A text file of URLs (one per line, '#' comments allowed) to process in parallel",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="How many URLs from --batch to run at once (default 3, max 10)",
    )


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
        help="Record a redacted provenance entry in the history log ('doc-dl doctor' shows where)",
    )
    parser.add_argument("--version", action="version", version=f"doc-dl {__version__}")
    _add_batch_options(parser)
    _add_output_modes(parser)
    return parser


def _archive_parser() -> DocDlArgumentParser:
    parser = DocDlArgumentParser(
        prog="doc-dl archive",
        description="Snapshot a news article or web page as a PDF",
    )
    parser.add_argument("url", nargs="?", help="Page URL to archive")
    parser.add_argument("-Url", dest="url_option", help="PowerShell-style URL compatibility form")
    parser.add_argument("-o", "--output", type=Path, help="Output file or directory")
    parser.add_argument(
        "--filename",
        dest="filename_template",
        help="Filename template using {title}, {ext}, {provider}, or {filename}",
    )
    parser.add_argument("--profile", default="default", help="Isolated browser profile name")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing output")
    parser.add_argument("--timeout", default="90s", help="Total timeout, such as 60s, 5m, or 1h")
    page_limit = parser.add_mutually_exclusive_group()
    page_limit.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help=(
            "Stop after this many pages, such as when a long feed page bunches the "
            "linked article with unrelated ones below it"
        ),
    )
    page_limit.add_argument(
        "--select-range",
        dest="select_range",
        help="Keep only this page range, such as 2-4 (1-indexed, inclusive)",
    )
    parser.add_argument(
        "--wait-for",
        dest="wait_for_selector",
        help=(
            "Wait for this CSS selector to become visible before capturing, for a "
            "page slower than the built-in settle heuristics expect"
        ),
    )
    parser.add_argument(
        "--no-metadata",
        action="store_true",
        help="Skip recording a history entry (recorded by default for archives)",
    )
    _add_batch_options(parser)
    _add_output_modes(parser)
    return parser


def _extract_pages_parser() -> DocDlArgumentParser:
    parser = DocDlArgumentParser(
        prog="doc-dl extract-pages",
        description="Copy a page range out of a local PDF into a new file",
    )
    parser.add_argument("input", type=Path, help="Path to the source PDF")
    parser.add_argument(
        "--pages", required=True, help="Page range to keep, such as 37-75 (1-indexed, inclusive)"
    )
    parser.add_argument(
        "-o", "--output", type=Path, help="Output PDF path (default: alongside the source)"
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing output file")
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


def parse_page_range(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", value)
    if not match:
        raise DocDlError(
            "invalid_arguments",
            f"Invalid --select-range value: {value!r}",
            detail="Use START-END, such as 2-4.",
        )
    start, end = int(match.group(1)), int(match.group(2))
    if start < 1 or end < start:
        raise DocDlError(
            "invalid_arguments",
            "--select-range must have START at least 1 and END at or after START",
        )
    return start, end


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


def _run_version(argv: Sequence[str]) -> int:
    args = _simple_parser("version").parse_args(list(argv))
    sink = _sink_from_args(args)
    if args.json:
        safe_print(
            json.dumps({"event": "version", "version": 1, "doc_dl": __version__}),
            file=sys.stdout,
        )
        return 0
    if args.quiet:
        safe_print(f"doc-dl {__version__}", file=sys.stdout)
        return 0

    safe_print("", file=sys.stdout)
    safe_print(
        render_banner(unicode_ok=sink.unicode_ok, color=sink.color),
        file=sys.stdout,
    )
    safe_print("", file=sys.stdout)
    version_line = f"   {__version__}"
    if RELEASE_NAME:
        suffix = f'  "{RELEASE_NAME}"'
        version_line += sink._colorize(suffix, DIM) if sink.color else suffix
    safe_print(sink._colorize(version_line, BOLD) if sink.color else version_line, file=sys.stdout)
    safe_print("", file=sys.stdout)
    return 0


def _run_batch(
    args: argparse.Namespace,
    sink: EventSink,
    worker: Callable[[str], Path],
) -> int:
    """Shared by `download --batch` and `archive --batch`: read the URL
    list, run `worker` several at a time, and print one line per result as
    it completes -- in completion order, not submission order, since that's
    what "several at a time" means. All the printing happens back on this
    thread, so two workers finishing together can never interleave their
    output."""
    from doc_dl.batch import BatchItem, clamp_concurrency, read_batch_urls, run_batch

    urls = read_batch_urls(args.batch)
    concurrency = clamp_concurrency(args.concurrency)
    if not args.quiet and not args.json:
        safe_print(
            f"  Processing {len(urls)} URL(s) from {args.batch}, {concurrency} at a time",
            file=sys.stdout,
        )
        safe_print("", file=sys.stdout)

    def on_result(index: int, total: int, item: BatchItem) -> None:
        if args.json:
            safe_print(
                json.dumps(
                    {
                        "event": "batch_item",
                        "version": 1,
                        "index": index,
                        "total": total,
                        "url": item.url,
                        "ok": item.ok,
                        "path": str(item.path) if item.path else None,
                        "detail": item.detail,
                    },
                    ensure_ascii=False,
                ),
                file=sys.stdout,
            )
            return
        if item.ok:
            if args.quiet:
                safe_print(str(item.path), file=sys.stdout)
                return
            glyph = sink._colorize(sink.glyphs.tick, GREEN) if sink.color else sink.glyphs.tick
            safe_print(f"  [{index}/{total}] {glyph} {item.path}", file=sys.stdout)
        else:
            glyph = sink._colorize(sink.glyphs.cross, RED) if sink.color else sink.glyphs.cross
            safe_print(f"  [{index}/{total}] {glyph} {item.url} -- {item.detail}", file=sys.stderr)

    results = run_batch(urls, worker, concurrency=concurrency, on_result=on_result)
    failed = sum(1 for item in results if not item.ok)
    if not args.quiet and not args.json:
        succeeded = len(results) - failed
        summary = f"  {succeeded} succeeded, {failed} failed"
        color = GREEN if failed == 0 else YELLOW
        safe_print("", file=sys.stdout)
        safe_print(sink._colorize(summary, color) if sink.color else summary, file=sys.stdout)
    return 0 if failed == 0 else 1


def _run_download(argv: Sequence[str]) -> int:
    args = _download_parser().parse_args(list(argv))
    sink = _sink_from_args(args)
    if args.retries < 0 or args.retries > 20:
        raise DocDlError("invalid_arguments", "Retries must be between 0 and 20")
    if args.batch:
        if args.url or args.url_option:
            raise DocDlError("invalid_arguments", "Pass either a URL or --batch, not both")
        return _run_download_batch(args, sink)
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


def _run_download_batch(args: argparse.Namespace, sink: EventSink) -> int:
    profile_slots = itertools.count()

    def worker(url: str) -> Path:
        per_sink = EventSink(quiet=True, stream=io.StringIO(), error_stream=io.StringIO())
        # Chromium locks its own user-data-dir, so two concurrent launches
        # sharing one profile fail outright ("Chromium could not be
        # started") rather than queueing -- each concurrent worker needs
        # its own. Bounded by batch size, reused on the next run.
        worker_profile = f"{args.profile}-batch-{next(profile_slots)}"
        request = DownloadRequest(
            url=url,
            output=args.output,
            filename_template=args.filename_template,
            original_only=args.original_only,
            allow_render=not args.original_only,
            forced_provider=args.forced_provider,
            profile=worker_profile,
            browser_enabled=not args.no_browser,
            resume=not args.no_resume,
            overwrite=args.overwrite,
            timeout_seconds=parse_duration(args.timeout),
            retries=args.retries,
            write_metadata=args.write_metadata,
        )
        return DownloadEngine(per_sink).download(request).path

    return _run_batch(args, sink, worker)


def _run_archive(argv: Sequence[str]) -> int:
    args = _archive_parser().parse_args(list(argv))
    sink = _sink_from_args(args)
    if args.max_pages is not None and args.max_pages < 1:
        raise DocDlError("invalid_arguments", "--max-pages must be 1 or greater")
    page_range = parse_page_range(args.select_range) if args.select_range else None
    if args.batch:
        if args.url or args.url_option:
            raise DocDlError("invalid_arguments", "Pass either a URL or --batch, not both")
        return _run_archive_batch(args, sink, page_range)
    request = ArchiveRequest(
        url=_resolve_url(args),
        output=args.output,
        filename_template=args.filename_template,
        profile=args.profile,
        timeout_seconds=parse_duration(args.timeout),
        overwrite=args.overwrite,
        write_metadata=not args.no_metadata,
        max_pages=args.max_pages,
        page_range=page_range,
        wait_for_selector=args.wait_for_selector,
    )
    PageArchiver(sink).archive(request)
    return 0


def _run_archive_batch(
    args: argparse.Namespace,
    sink: EventSink,
    page_range: tuple[int, int] | None,
) -> int:
    profile_slots = itertools.count()

    def worker(url: str) -> Path:
        per_sink = EventSink(quiet=True, stream=io.StringIO(), error_stream=io.StringIO())
        worker_profile = f"{args.profile}-batch-{next(profile_slots)}"
        request = ArchiveRequest(
            url=url,
            output=args.output,
            filename_template=args.filename_template,
            profile=worker_profile,
            timeout_seconds=parse_duration(args.timeout),
            overwrite=args.overwrite,
            write_metadata=not args.no_metadata,
            max_pages=args.max_pages,
            page_range=page_range,
            wait_for_selector=args.wait_for_selector,
        )
        return PageArchiver(per_sink).archive(request).path

    return _run_batch(args, sink, worker)


def _run_extract_pages(argv: Sequence[str]) -> int:
    args = _extract_pages_parser().parse_args(list(argv))
    sink = _sink_from_args(args)
    source = args.input.expanduser()
    if not source.is_file():
        raise DocDlError("invalid_arguments", f"No such file: {source}")
    page_range = parse_page_range(args.pages)
    default_name = f"{source.stem}-pages-{page_range[0]}-{page_range[1]}{source.suffix or '.pdf'}"
    output = resolve_output_path(args.output, default_name, overwrite=args.overwrite)
    page_count = extract_pdf_pages(source, page_range, output)
    size = output.stat().st_size
    sink.emit(
        "complete",
        path=str(output),
        filename=output.name,
        media_type="application/pdf",
        bytes=size,
        source_url=str(source),
        page_count=page_count,
        facts=[format_bytes(size), f"{page_count} pages", f"from {source.name}"],
    )
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
    safe_print('  doc-dl archive "https://example.com/news/some-article"', file=sys.stdout)
    safe_print("  doc-dl extract-pages book.pdf --pages 37-75", file=sys.stdout)
    safe_print("  doc-dl doctor", file=sys.stdout)


def run(argv: Sequence[str] | None = None) -> int:
    enable_utf8_output()
    configure_browsers_path()
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        _print_bare_invocation_help()
        return 0
    if "--version" in arguments:
        # Route through the same renderer as `doc-dl version` rather than
        # argparse's built-in action, which would print a bare version
        # string and exit before the banner ever gets a chance to show.
        return _run_version([a for a in arguments if a not in {"--version"}])
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
            return _run_version(command_args)
        if command == "archive":
            return _run_archive(command_args)
        if command == "extract-pages":
            return _run_extract_pages(command_args)
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
