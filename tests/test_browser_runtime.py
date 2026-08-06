from __future__ import annotations

import io
import logging
from pathlib import Path

import doc_dl.browser as browser_module
from doc_dl.browser import BrowserExtractor
from doc_dl.config import StatePaths
from doc_dl.events import EventSink


def quiet_sink() -> EventSink:
    return EventSink(quiet=True, stream=io.StringIO(), error_stream=io.StringIO())


def test_importing_browser_silences_asyncio_teardown_noise() -> None:
    assert browser_module is not None
    assert logging.getLogger("asyncio").level == logging.CRITICAL


def test_ensure_chromium_skips_install_when_already_present(tmp_path: Path, monkeypatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr("doc_dl.browser.configure_browsers_path", lambda state: None)
    monkeypatch.setattr("doc_dl.browser.is_chromium_installed", lambda: True)
    monkeypatch.setattr("doc_dl.browser.install_chromium", lambda *a, **k: calls.append((a, k)))

    extractor = BrowserExtractor(quiet_sink(), StatePaths(tmp_path / "state"))
    extractor._ensure_chromium()

    assert calls == []


def test_ensure_chromium_installs_when_missing(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[object, object]] = []
    monkeypatch.setattr("doc_dl.browser.configure_browsers_path", lambda state: None)
    monkeypatch.setattr("doc_dl.browser.is_chromium_installed", lambda: False)
    monkeypatch.setattr(
        "doc_dl.browser.install_chromium",
        lambda sink, *, state=None: calls.append((sink, state)),
    )

    sink = quiet_sink()
    state = StatePaths(tmp_path / "state")
    extractor = BrowserExtractor(sink, state)
    extractor._ensure_chromium()

    assert calls == [(sink, state)]
