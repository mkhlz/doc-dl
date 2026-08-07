from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

from doc_dl.errors import Severity, remedy_for, severity_of
from doc_dl.redaction import redact_headers, redact_url
from doc_dl.ui import (
    BOLD,
    CLEAR_LINE,
    CYAN,
    DIM,
    GREEN,
    MAGENTA,
    RESET,
    YELLOW,
    Glyphs,
    Spinner,
    elapsed_since,
    env_disables_color,
    format_bytes,
    format_rate,
    render_bar,
    stream_handles_unicode,
    windows_ansi_enabled,
)
from doc_dl.ui import RED as _RED

_SEVERITY_COLOR = {
    Severity.ACTIONABLE: YELLOW,
    Severity.FAILED: _RED,
    Severity.MISTAKE: DIM,
    Severity.BUG: MAGENTA,
}


def safe_print(value: object, *, file: TextIO, flush: bool = True, end: str = "\n") -> None:
    text = str(value)
    encoding = getattr(file, "encoding", None)
    if encoding:
        try:
            text.encode(encoding)
        except (LookupError, UnicodeEncodeError):
            text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    print(text, file=file, end=end, flush=flush)


def _json_default(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return str(value)


def _redact_payload(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        if key and key.casefold() == "headers":
            return redact_headers({str(k): str(v) for k, v in value.items()})
        return {str(k): _redact_payload(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_payload(item, key) for item in value]
    if isinstance(value, tuple):
        return [_redact_payload(item, key) for item in value]
    if isinstance(value, str) and key and (key.endswith("url") or key == "url"):
        return redact_url(value)
    return value


def render_progress_bar(
    downloaded: float,
    total: float | None,
    unit: str,
    glyphs: Glyphs | None = None,
) -> str:
    """One progress line: bar, percentage, and whichever counts make sense."""
    marks = glyphs or Glyphs.plain()
    ratio = min(1.0, downloaded / total) if total else 0.0
    bar = render_bar(ratio, marks)
    percent = f"{int(ratio * 100):3d}%"
    if unit == "pages":
        counts = f"{int(downloaded)}/{int(total)}" if total else f"{int(downloaded)}"
    else:
        counts = (
            f"{format_bytes(downloaded)}/{format_bytes(total)}"
            if total
            else format_bytes(downloaded)
        )
    return f"[{bar}] {percent}  {counts}"


class EventSink:
    def __init__(
        self,
        *,
        json_mode: bool = False,
        quiet: bool = False,
        verbose: bool = False,
        stream: TextIO | None = None,
        error_stream: TextIO | None = None,
        color: bool | None = None,
        unicode_ok: bool | None = None,
    ) -> None:
        self.json_mode = json_mode
        self.quiet = quiet
        self.verbose = verbose
        self.stream = stream or sys.stdout
        self.error_stream = error_stream or sys.stderr
        self._progress_active = False
        self._spinner: Spinner | None = None
        self._started = time.monotonic()
        self.interactive = self._detect_interactive()
        self.color = self._detect_color_support() if color is None else color
        if unicode_ok is None:
            unicode_ok = stream_handles_unicode(self.stream)
        self.unicode_ok = bool(unicode_ok)
        self.glyphs = Glyphs.rich() if self.unicode_ok else Glyphs.plain()

    def _detect_interactive(self) -> bool:
        if self.json_mode:
            return False
        isatty = getattr(self.stream, "isatty", None)
        return bool(callable(isatty) and isatty())

    def _detect_color_support(self) -> bool:
        if not self.interactive or env_disables_color():
            return False
        return windows_ansi_enabled()

    def _colorize(self, text: str, *codes: str) -> str:
        if not self.color or not codes:
            return text
        return f"{''.join(codes)}{text}{RESET}"

    def spinner(self, text: str) -> Spinner:
        """A spinner for a phase with nothing measurable to report."""
        spinner = Spinner(
            self.stream,
            self.glyphs,
            enabled=self.interactive and not self.quiet and not self.json_mode,
            color=self.color,
        )
        spinner.start(text)
        return spinner

    def _end_progress_line(self) -> None:
        if self._progress_active:
            safe_print("", file=self.stream, end="\n")
            self._progress_active = False

    def _stop_spinner(self) -> None:
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None

    def _quiesce(self) -> None:
        """Clear any live line so the next output starts on clean ground."""
        self._stop_spinner()
        self._end_progress_line()

    def emit(self, event: str, **payload: Any) -> None:
        data = _redact_payload({"event": event, "version": 1, **payload})
        if self.json_mode:
            safe_print(
                json.dumps(data, ensure_ascii=False, default=_json_default, sort_keys=True),
                file=self.stream,
            )
            return

        if event == "error":
            self._render_error(data)
            return

        if self.quiet and event != "complete":
            return
        if event in {"strategy", "candidate", "retry", "verification"} and not self.verbose:
            return

        if event == "start":
            self._begin_resolving()
            return
        if event == "document_info":
            self._render_document_info(data)
            return
        if event == "download_progress":
            self._render_progress(data)
            return
        if event == "warning":
            self._quiesce()
            glyph = self._colorize(self.glyphs.warn, YELLOW)
            safe_print(f"  {glyph} {data.get('message', '')}", file=self.stream)
            return
        if event == "complete":
            self._render_complete(data)
            return

        self._quiesce()
        message = data.get("message")
        if message:
            safe_print(f"  {self._colorize(str(message), DIM)}", file=self.stream)

    def _begin_resolving(self) -> None:
        """Hold a spinner while the link is being worked out, so a slow site
        does not look like a hung program."""
        if self.interactive and not self.quiet:
            self._spinner = self.spinner("Resolving link")

    def _render_document_info(self, data: dict[str, Any]) -> None:
        """What the link turned out to be, before any bytes move."""
        self._quiesce()
        lines: list[str] = [""]
        site = data.get("site")
        if site:
            lines.append(f"  {self._colorize(str(site), DIM)}")
        title = data.get("title")
        if title:
            lines.append(f"  {self._colorize(str(title), BOLD)}")
        facts = [str(item) for item in data.get("facts", []) if item]
        if facts:
            joined = f" {self.glyphs.bullet} ".join(facts)
            lines.append(f"  {self._colorize(joined, DIM)}")
        lines.append("")
        safe_print("\n".join(lines), file=self.stream, end="\n")

    def _render_progress(self, data: dict[str, Any]) -> None:
        self._stop_spinner()
        downloaded = float(data.get("downloaded", 0))
        total = data.get("total")
        unit = str(data.get("unit", "bytes"))
        line = render_progress_bar(downloaded, total, unit, self.glyphs)

        trailing = ""
        if unit == "bytes":
            rate = format_rate(downloaded, elapsed_since(self._started))
            if rate:
                trailing = f"  {rate}"
        elif unit == "pages":
            trailing = "  pages"

        if not self.interactive:
            # Piped or redirected: no cursor control, one line per update.
            safe_print(f"  {line}{trailing}", file=self.stream)
            return

        finished = bool(total) and downloaded >= float(total)
        color = GREEN if finished else CYAN
        body = self._colorize(line, color) + self._colorize(trailing, DIM)
        safe_print(f"\r{CLEAR_LINE}  {body}", file=self.stream, end="")
        self._progress_active = True

    def _render_complete(self, data: dict[str, Any]) -> None:
        self._quiesce()
        message = data.get("message")
        path_text = str(data.get("path", ""))

        if self.quiet:
            # Scripts read this: quiet mode stays exactly one line, the path,
            # with nothing decorative in front of it.
            safe_print(message or path_text, file=self.stream)
            return

        if message:
            # Browser install and cleanup report a sentence, not a document.
            glyph = self._colorize(self.glyphs.tick, GREEN)
            safe_print(f"  {glyph} {message}", file=self.stream)
            return

        path = Path(path_text)
        glyph = self._colorize(self.glyphs.tick, GREEN)
        safe_print(f"  {glyph} {self._colorize(path.name, BOLD)}", file=self.stream)

        facts = [str(item) for item in data.get("facts", []) if item]
        if facts:
            joined = f" {self.glyphs.bullet} ".join(facts)
            safe_print(f"    {self._colorize(joined, DIM)}", file=self.stream)
        parent = str(path.parent)
        if parent and parent != ".":
            arrow = self.glyphs.arrow
            safe_print(f"    {self._colorize(f'{arrow} {parent}', DIM)}", file=self.stream)

    def _render_error(self, data: dict[str, Any]) -> None:
        self._quiesce()
        identifier = str(data.get("error", "internal_error"))
        severity = severity_of(identifier)
        color = _SEVERITY_COLOR.get(severity, _RED)
        glyph = {
            Severity.ACTIONABLE: self.glyphs.warn,
            Severity.BUG: self.glyphs.bug,
        }.get(severity, self.glyphs.cross)

        message = str(data.get("message", identifier))
        safe_print(
            f"  {self._colorize(glyph, color)} {self._colorize(message, BOLD)}",
            file=self.error_stream,
        )
        for line in (data.get("detail"), remedy_for(identifier)):
            if line:
                safe_print(f"    {self._colorize(str(line), DIM)}", file=self.error_stream)
