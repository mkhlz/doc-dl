from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from doc_dl.archive import PageArchiver
from doc_dl.events import EventSink
from doc_dl.models import ArchiveRequest, Provenance
from tests.fixture_server import FixtureServer


def quiet_sink() -> EventSink:
    return EventSink(quiet=True, stream=io.StringIO(), error_stream=io.StringIO())


def read_sidecar(path: Path) -> dict:
    sidecar = path.with_name(f"{path.name}.doc-dl.json")
    return json.loads(sidecar.read_text(encoding="utf-8"))


def assert_image_backed_pages(path: Path) -> None:
    reader = PdfReader(str(path), strict=False)
    assert len(reader.pages) >= 1
    assert all(len(page.images) >= 1 for page in reader.pages)


@pytest.mark.browser
def test_archive_captures_a_page_as_screenshots(
    fixture_server: FixtureServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOC_DL_STATE_DIR", str(tmp_path / "state"))
    result = PageArchiver(quiet_sink()).archive(
        ArchiveRequest(
            url=fixture_server.url("/site/article.html"),
            output=tmp_path / "output",
            timeout_seconds=45,
        )
    )
    assert result.provenance == Provenance.CAPTURED
    assert result.title == "Harbor City Approves New Transit Line"
    assert result.byline == "Jordan Rivera"
    assert result.site_name == "The Harbor Gazette"
    assert result.paywall_suspected is False
    assert result.path.exists()
    assert_image_backed_pages(result.path)

    payload = read_sidecar(result.path)
    assert payload["title"] == "Harbor City Approves New Transit Line"
    assert payload["paywall_suspected"] is False


@pytest.mark.browser
def test_archive_captures_a_sparse_page_too(
    fixture_server: FixtureServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOC_DL_STATE_DIR", str(tmp_path / "state"))
    result = PageArchiver(quiet_sink()).archive(
        ArchiveRequest(
            url=fixture_server.url("/site/sparse-page.html"),
            output=tmp_path / "output",
            timeout_seconds=45,
        )
    )
    assert result.provenance == Provenance.CAPTURED
    assert_image_backed_pages(result.path)


@pytest.mark.browser
def test_archive_flags_paywall_and_still_captures(
    fixture_server: FixtureServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOC_DL_STATE_DIR", str(tmp_path / "state"))
    result = PageArchiver(quiet_sink()).archive(
        ArchiveRequest(
            url=fixture_server.url("/site/paywalled-article.html"),
            output=tmp_path / "output",
            timeout_seconds=45,
        )
    )
    assert result.paywall_suspected is True
    assert result.path.exists()
    payload = read_sidecar(result.path)
    assert payload["paywall_suspected"] is True


@pytest.mark.browser
def test_archive_expands_read_more_before_capturing(
    fixture_server: FixtureServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOC_DL_STATE_DIR", str(tmp_path / "state"))
    result = PageArchiver(quiet_sink()).archive(
        ArchiveRequest(
            url=fixture_server.url("/site/read-more.html"),
            output=tmp_path / "output",
            timeout_seconds=45,
        )
    )
    assert_image_backed_pages(result.path)
    # The teaser alone fits one viewport; the hidden content revealed by
    # clicking "Read More" is tall enough on its own to force a second page,
    # so more than one page proves the click actually happened.
    reader = PdfReader(str(result.path), strict=False)
    assert len(reader.pages) >= 2


@pytest.mark.browser
def test_expand_ignores_aria_expanded_overflow_menus(
    fixture_server: FixtureServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """news.google.com's per-story "..." button and its own menu/search
    controls all carry aria-expanded="false" without being a "read more"
    toggle at all; clicking them just pops open an unrelated share/save
    menu that then sits in the screenshot. Only text-matched buttons like
    the fixture's real "Read More" should ever be clicked."""
    import time

    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("DOC_DL_STATE_DIR", str(tmp_path / "state"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="chromium")
        page = browser.new_page(viewport={"width": 1280, "height": 1400})
        page.goto(fixture_server.url("/site/read-more.html"), wait_until="domcontentloaded")
        PageArchiver._expand_collapsed_content(page, time.monotonic() + 10)
        rest_visible = page.evaluate(
            "() => getComputedStyle(document.getElementById('rest')).display !== 'none'"
        )
        menu_visible = page.evaluate(
            "() => getComputedStyle(document.getElementById('menu-popup')).display !== 'none'"
        )
        browser.close()
    assert rest_visible is True
    assert menu_visible is False


def test_archive_requires_a_url() -> None:
    from doc_dl.cli import run

    exit_code = run(["archive", "--quiet"])
    assert exit_code == 2
