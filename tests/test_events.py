from __future__ import annotations

import io
import os

from doc_dl.events import EventSink, render_progress_bar, safe_print
from doc_dl.ui import Glyphs


def test_safe_print_replaces_characters_unsupported_by_console() -> None:
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="ascii")

    safe_print("message with ┌ unicode", file=stream)
    stream.flush()

    assert buffer.getvalue().decode("ascii") == f"message with ? unicode{os.linesep}"


class _TtyStream(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_json_mode_never_uses_color_regardless_of_tty() -> None:
    sink = EventSink(json_mode=True, stream=_TtyStream())
    assert sink.color is False


def test_no_color_env_disables_color(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    sink = EventSink(stream=_TtyStream())
    assert sink.color is False


def test_non_tty_stream_disables_color() -> None:
    sink = EventSink(stream=io.StringIO())
    assert sink.color is False


def test_render_progress_bar_bytes() -> None:
    line = render_progress_bar(50, 100, "bytes")
    assert "50%" in line
    assert "50B/100B" in line


def test_render_progress_bar_pages() -> None:
    line = render_progress_bar(5, 20, "pages")
    assert "5/20" in line
    assert "25%" in line


def test_progress_bar_uses_ascii_by_default() -> None:
    # The plain glyph set must never emit block characters, which legacy
    # Windows code pages render as question marks.
    line = render_progress_bar(5, 20, "pages")
    assert "#" in line and "-" in line
    assert "█" not in line


def test_progress_bar_uses_blocks_when_the_terminal_can_show_them() -> None:
    line = render_progress_bar(5, 20, "pages", Glyphs.rich())
    assert "█" in line and "░" in line


def test_download_progress_updates_in_place_on_a_tty() -> None:
    stream = _TtyStream()
    sink = EventSink(stream=stream, color=False)

    sink.emit("download_progress", downloaded=10, total=100, unit="bytes")

    output = stream.getvalue()
    assert output.startswith("\r")
    assert not output.endswith("\n")


def test_download_progress_prints_plain_lines_when_piped() -> None:
    stream = io.StringIO()
    sink = EventSink(stream=stream, color=False)

    sink.emit("download_progress", downloaded=10, total=100, unit="bytes")

    output = stream.getvalue()
    assert "\r" not in output
    assert output.endswith("\n")


def test_complete_event_prefers_message_over_path() -> None:
    stream = io.StringIO()
    sink = EventSink(stream=stream, color=False)

    sink.emit(
        "complete",
        message="Chromium is already installed at /fake/chrome",
        path="/fake/chrome",
    )

    assert "already installed" in stream.getvalue()


def test_complete_event_summarizes_the_document() -> None:
    stream = io.StringIO()
    sink = EventSink(stream=stream, color=False)

    sink.emit(
        "complete",
        path="/output/document.pdf",
        facts=["1.2MB", "12 pages", "original file", "3.1s"],
    )

    output = stream.getvalue()
    assert "document.pdf" in output
    assert "12 pages" in output
    assert "original file" in output


def test_quiet_mode_prints_only_the_path() -> None:
    # Scripts pipe this: quiet output must stay one bare line.
    stream = io.StringIO()
    sink = EventSink(stream=stream, quiet=True, color=False)

    sink.emit("complete", path="/output/document.pdf", facts=["1.2MB", "original file"])

    assert stream.getvalue().strip() == "/output/document.pdf"


def test_error_shows_severity_and_a_remedy() -> None:
    errors = io.StringIO()
    sink = EventSink(stream=io.StringIO(), error_stream=errors, color=False)

    sink.emit("error", error="output_exists", message="That file already exists")

    output = errors.getvalue()
    assert "That file already exists" in output
    assert "--overwrite" in output


def test_bug_errors_are_marked_as_ours() -> None:
    errors = io.StringIO()
    sink = EventSink(stream=io.StringIO(), error_stream=errors, color=False)

    sink.emit("error", error="internal_error", message="doc-dl hit a bug")

    assert "github.com/mkhlz/doc-dl/issues" in errors.getvalue()
