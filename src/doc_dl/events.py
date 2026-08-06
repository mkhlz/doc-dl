from __future__ import annotations

import ctypes
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

from doc_dl.redaction import redact_headers, redact_url

_BAR_WIDTH = 24
_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_RED = "\x1b[31m"
_GREEN = "\x1b[32m"
_CYAN = "\x1b[36m"
_CLEAR_LINE = "\x1b[K"

_windows_ansi_ready: bool | None = None


def _windows_ansi_enabled() -> bool:
    """Turn on VT100 escape processing for the classic Windows console host.

    Modern Windows Terminal and PowerShell 7 already support ANSI codes, but
    the legacy conhost.exe needs this opt-in, done once per process.
    """
    global _windows_ansi_ready
    if _windows_ansi_ready is not None:
        return _windows_ansi_ready
    if sys.platform != "win32":
        _windows_ansi_ready = True
        return True
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            _windows_ansi_ready = False
        else:
            enable_virtual_terminal_processing = 0x0004
            _windows_ansi_ready = bool(
                kernel32.SetConsoleMode(handle, mode.value | enable_virtual_terminal_processing)
            )
    except Exception:
        _windows_ansi_ready = False
    return _windows_ansi_ready


def format_bytes(value: float) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def render_progress_bar(downloaded: float, total: float | None, unit: str) -> str:
    ratio = min(1.0, downloaded / total) if total else 0.0
    filled = int(_BAR_WIDTH * ratio)
    # Plain ASCII, not Unicode block characters: those render as "?????" under
    # the legacy codepages several Windows terminals still default to.
    bar = "#" * filled + "-" * (_BAR_WIDTH - filled)
    percent = int(ratio * 100)
    if unit == "pages":
        label = "Reconstructing"
        counts = f"{int(downloaded)}/{int(total)} pages" if total else f"{int(downloaded)} pages"
    else:
        label = "Downloading"
        counts = (
            f"{format_bytes(downloaded)}/{format_bytes(total)}"
            if total
            else format_bytes(downloaded)
        )
    return f"{label}  [{bar}]  {counts} ({percent}%)"


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
    ) -> None:
        self.json_mode = json_mode
        self.quiet = quiet
        self.verbose = verbose
        self.stream = stream or sys.stdout
        self.error_stream = error_stream or sys.stderr
        self._progress_active = False
        self.interactive = self._detect_interactive()
        if color is not None:
            self.color = color
        else:
            self.color = self._detect_color_support()

    def _detect_interactive(self) -> bool:
        if self.json_mode:
            return False
        isatty = getattr(self.stream, "isatty", None)
        return bool(callable(isatty) and isatty())

    def _detect_color_support(self) -> bool:
        if not self.interactive:
            return False
        if os.environ.get("NO_COLOR"):
            return False
        if os.environ.get("TERM") == "dumb":
            return False
        return _windows_ansi_enabled()

    def _colorize(self, text: str, *codes: str) -> str:
        if not self.color:
            return text
        return f"{''.join(codes)}{text}{_RESET}"

    def _end_progress_line(self) -> None:
        if self._progress_active:
            safe_print("", file=self.stream, end="\n")
            self._progress_active = False

    def emit(self, event: str, **payload: Any) -> None:
        data = _redact_payload({"event": event, "version": 1, **payload})
        if self.json_mode:
            safe_print(
                json.dumps(data, ensure_ascii=False, default=_json_default, sort_keys=True),
                file=self.stream,
            )
            return

        if event == "error":
            self._end_progress_line()
            message = str(data.get("message", data.get("error", "Unknown error")))
            safe_print(
                self._colorize(f"ERROR: {message}", _BOLD, _RED),
                file=self.error_stream,
            )
            detail = data.get("detail")
            if detail:
                safe_print(self._colorize(f"  {detail}", _DIM), file=self.error_stream)
            return

        if self.quiet and event != "complete":
            return
        if event in {"strategy", "candidate", "retry", "verification"} and not self.verbose:
            return

        if event == "download_progress":
            downloaded = data.get("downloaded", 0)
            total = data.get("total")
            unit = str(data.get("unit", "bytes"))
            line = render_progress_bar(float(downloaded), total, unit)
            if not self.interactive:
                # Piped or redirected output: no cursor control, one line per update.
                safe_print(line, file=self.stream)
                return
            color_codes = (_GREEN,) if total and downloaded >= total else (_CYAN,)
            safe_print(
                f"\r{_CLEAR_LINE}{self._colorize(line, *color_codes)}",
                file=self.stream,
                end="",
            )
            self._progress_active = True
            return

        self._end_progress_line()
        message = data.get("message")
        if event == "complete":
            text = str(message) if message else str(data.get("path", ""))
            safe_print(self._colorize(text, _BOLD, _GREEN), file=self.stream)
        elif message:
            safe_print(message, file=self.stream)
