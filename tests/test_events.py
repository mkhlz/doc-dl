from __future__ import annotations

import io
import os

from doc_dl.events import EventSink, render_progress_bar, safe_print


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
    assert "Downloading" in line
    assert "50%" in line


def test_render_progress_bar_pages() -> None:
    line = render_progress_bar(5, 20, "pages")
    assert "5/20 pages" in line
    assert "25%" in line


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


def test_complete_event_falls_back_to_path_without_message() -> None:
    stream = io.StringIO()
    sink = EventSink(stream=stream, color=False)

    sink.emit("complete", path="/output/document.pdf")

    assert stream.getvalue().strip() == "/output/document.pdf"
