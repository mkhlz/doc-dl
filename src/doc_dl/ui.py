"""Terminal presentation: capability detection, glyphs, banner, spinner.

Everything decorative is chosen here so the rest of the program never has to
ask what the terminal can render. A capable terminal gets block art, braille
spinners and colour; anything else degrades to plain ASCII on its own.
"""

from __future__ import annotations

import ctypes
import itertools
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import TextIO

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"
BRIGHT_BLUE = "\x1b[94m"
CLEAR_LINE = "\x1b[K"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"

BAR_WIDTH = 24
SPINNER_INTERVAL = 0.08

_windows_ansi_ready: bool | None = None


def windows_ansi_enabled() -> bool:
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


def enable_utf8_output() -> None:
    """Match Python's output encoding to a console that already speaks UTF-8.

    A frozen build does not inherit the interpreter's UTF-8 default, so on
    Windows it would encode block and braille characters as question marks even
    in a terminal perfectly able to draw them. The encoding is only raised when
    the console reports code page 65001, so a console that genuinely cannot
    display them is left alone.
    """
    if sys.platform != "win32":
        return
    try:
        if ctypes.windll.kernel32.GetConsoleOutputCP() != 65001:  # type: ignore[attr-defined]
            return
    except Exception:
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            continue


def _glyph_fallback_capable() -> bool:
    """Whether this console substitutes a fallback font for missing glyphs.

    Windows Terminal, ConEmu, and VS Code's integrated terminal all do font
    fallback, so a codepoint missing from the primary font still draws from
    another one. The classic conhost window (the blue "Windows PowerShell"
    console, or plain cmd.exe) does not: if the selected font -- usually
    Consolas, which has no braille or check-mark glyphs -- lacks a codepoint,
    it draws a tofu box even though the encoding itself round-trips fine.
    """
    if sys.platform != "win32":
        return True
    return bool(
        os.environ.get("WT_SESSION")
        or os.environ.get("ConEmuANSI") == "ON"  # noqa: SIM112 -- ConEmu's real casing
        or os.environ.get("TERM_PROGRAM")
    )


def stream_handles_unicode(stream: TextIO) -> bool:
    """Whether decorative Unicode survives this stream's encoding and font.

    Legacy Windows code pages turn block and braille characters into "?????",
    so the glyphs are tested against the real encoding rather than assumed.
    A correct encoding is not enough on its own: the classic Windows console
    can round-trip the bytes and still fail to draw them, so that is checked
    too.
    """
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return False
    try:
        "█░⠋✔▲✖·→".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return _glyph_fallback_capable()


@dataclass(frozen=True, slots=True)
class Glyphs:
    """The decorative characters, in a rich and a plain edition."""

    tick: str
    warn: str
    cross: str
    bug: str
    bar_full: str
    bar_empty: str
    bullet: str
    arrow: str
    spinner: tuple[str, ...]

    @classmethod
    def rich(cls) -> Glyphs:
        return cls(
            tick="✔",
            warn="▲",
            cross="✖",
            bug="✖",
            bar_full="█",
            bar_empty="░",
            bullet="·",
            arrow="→",
            spinner=("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"),
        )

    @classmethod
    def plain(cls) -> Glyphs:
        return cls(
            tick="[ok]",
            warn="[!] ",
            cross="[x] ",
            bug="[x] ",
            bar_full="#",
            bar_empty="-",
            bullet="-",
            arrow="->",
            spinner=("|", "/", "-", "\\"),
        )


BANNER_RICH = r"""   ██████╗  ██████╗  ██████╗      ██████╗  ██╗
   ██╔══██╗██╔═══██╗██╔════╝      ██╔══██╗ ██║
   ██║  ██║██║   ██║██║     █████╗██║  ██║ ██║
   ██║  ██║██║   ██║██║     ╚════╝██║  ██║ ██║
   ██████╔╝╚██████╔╝╚██████╗      ██████╔╝ ███████╗
   ╚═════╝  ╚═════╝  ╚═════╝      ╚═════╝  ╚══════╝"""

BANNER_PLAIN = r"""    _                 _ _
   | |               | | |
 __| | ___   ___    _| | |
/ _` |/ _ \ / __|  / _` | |
\__,_|\___/ \___|  \__,_|_|"""

TAGLINE = "a resilient command-line document downloader"


def render_banner(*, unicode_ok: bool, color: bool) -> str:
    """The wordmark, shown only on first install and `doc-dl version`."""
    art = BANNER_RICH if unicode_ok else BANNER_PLAIN
    if not color:
        return f"{art}\n\n        {TAGLINE}"

    lines = []
    for line in art.split("\n"):
        # The wordmark reads "doc-" in white and "dl" in the brand blue; the
        # split point differs between the two editions.
        split = 33 if unicode_ok else 18
        head, tail = line[:split], line[split:]
        lines.append(f"{BOLD}{head}{RESET}{BRIGHT_BLUE}{BOLD}{tail}{RESET}")
    return "\n".join(lines) + f"\n\n        {DIM}{TAGLINE}{RESET}"


def format_bytes(value: float) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m {rest}s"


def format_rate(bytes_done: float, elapsed: float) -> str | None:
    if elapsed <= 0 or bytes_done <= 0:
        return None
    return f"{format_bytes(bytes_done / elapsed)}/s"


class Spinner:
    """An ora-style spinner for work with no measurable progress.

    Runs on a daemon thread so a slow network call still animates, and writes
    nothing at all when the stream is not an interactive terminal.
    """

    def __init__(
        self,
        stream: TextIO,
        glyphs: Glyphs,
        *,
        enabled: bool,
        color: bool,
    ) -> None:
        self.stream = stream
        self.glyphs = glyphs
        self.enabled = enabled
        self.color = color
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._text = ""

    def start(self, text: str) -> None:
        self._text = text
        if not self.enabled:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        frames = itertools.cycle(self.glyphs.spinner)
        try:
            self.stream.write(HIDE_CURSOR)
            while not self._stop.is_set():
                frame = next(frames)
                if self.color:
                    frame = f"{CYAN}{frame}{RESET}"
                self.stream.write(f"\r{CLEAR_LINE}  {frame} {self._text}")
                self.stream.flush()
                self._stop.wait(SPINNER_INTERVAL)
        except Exception:
            # A spinner must never be the reason a download fails.
            return

    def stop(self) -> None:
        if not self.enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None
        try:
            self.stream.write(f"\r{CLEAR_LINE}{SHOW_CURSOR}")
            self.stream.flush()
        except Exception:
            return

    def __enter__(self) -> Spinner:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()


def render_bar(ratio: float, glyphs: Glyphs, width: int = BAR_WIDTH) -> str:
    ratio = max(0.0, min(1.0, ratio))
    filled = int(width * ratio)
    return glyphs.bar_full * filled + glyphs.bar_empty * (width - filled)


def elapsed_since(started: float) -> float:
    return max(0.0, time.monotonic() - started)


def env_disables_color() -> bool:
    return bool(os.environ.get("NO_COLOR")) or os.environ.get("TERM") == "dumb"
