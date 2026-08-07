from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from doc_dl.archive import PageArchiver
from doc_dl.errors import DocDlError
from doc_dl.events import EventSink
from doc_dl.models import ArchiveRequest, Provenance
from tests.fixture_server import FixtureServer


def quiet_sink() -> EventSink:
    return EventSink(quiet=True, stream=io.StringIO(), error_stream=io.StringIO())


def read_sidecar(path: Path) -> dict:
    sidecar = path.with_name(f"{path.name}.doc-dl.json")
    return json.loads(sidecar.read_text(encoding="utf-8"))


@pytest.mark.browser
def test_archive_extracts_readable_article_as_pdf(
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
    assert result.provenance == Provenance.PRINTED
    assert result.title == "Harbor City Approves New Transit Line"
    assert result.byline == "Jordan Rivera"
    assert result.site_name == "The Harbor Gazette"
    assert result.paywall_suspected is False
    assert result.path.exists()

    reader = PdfReader(str(result.path), strict=False)
    assert len(reader.pages) >= 1
    text = reader.pages[0].extract_text() or ""
    assert "Harbor City" in text
    # Sidebar clutter (promo/related asides) should not have won the scoring.
    assert "newsletter" not in text.lower()

    payload = read_sidecar(result.path)
    assert payload["title"] == "Harbor City Approves New Transit Line"
    assert payload["paywall_suspected"] is False


@pytest.mark.browser
def test_archive_falls_back_to_screenshot_for_sparse_page(
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
    reader = PdfReader(str(result.path), strict=False)
    assert len(reader.pages) == 1
    assert len(reader.pages[0].images) >= 1


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
def test_archive_readability_mode_raises_when_text_is_too_sparse(
    fixture_server: FixtureServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOC_DL_STATE_DIR", str(tmp_path / "state"))
    with pytest.raises(DocDlError) as excinfo:
        PageArchiver(quiet_sink()).archive(
            ArchiveRequest(
                url=fixture_server.url("/site/sparse-page.html"),
                output=tmp_path / "output",
                mode="readability",
                timeout_seconds=45,
            )
        )
    assert excinfo.value.identifier == "render_incomplete"


def test_archive_requires_a_url() -> None:
    from doc_dl.cli import run

    exit_code = run(["archive", "--quiet"])
    assert exit_code == 2
