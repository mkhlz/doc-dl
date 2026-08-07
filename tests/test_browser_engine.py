from __future__ import annotations

import io
from pathlib import Path

import pytest
from pypdf import PdfReader

from doc_dl.engine import DownloadEngine
from doc_dl.errors import DocDlError
from doc_dl.events import EventSink
from doc_dl.models import DownloadRequest, Provenance
from tests.fixture_server import FixtureServer


def quiet_sink() -> EventSink:
    return EventSink(quiet=True, stream=io.StringIO(), error_stream=io.StringIO())


def assert_image_backed_pages(path: Path, expected_pages: int) -> None:
    reader = PdfReader(str(path), strict=False)
    assert len(reader.pages) == expected_pages
    assert all(len(page.images) >= 1 for page in reader.pages)


@pytest.mark.browser
def test_javascript_browser_download(
    fixture_server: FixtureServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOC_DL_STATE_DIR", str(tmp_path / "state"))
    result = DownloadEngine(quiet_sink()).download(
        DownloadRequest(
            url=fixture_server.url("/site/js-download.html"),
            output=tmp_path / "output",
            timeout_seconds=45,
        )
    )
    assert result.provenance == Provenance.ORIGINAL
    assert result.page_count == 1


@pytest.mark.browser
def test_xhr_pdf_is_captured_as_original(
    fixture_server: FixtureServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOC_DL_STATE_DIR", str(tmp_path / "state"))
    result = DownloadEngine(quiet_sink()).download(
        DownloadRequest(
            url=fixture_server.url("/site/xhr-viewer.html"),
            output=tmp_path / "output",
            timeout_seconds=45,
        )
    )
    assert result.provenance == Provenance.ORIGINAL
    assert result.filename == "xhr.pdf"


@pytest.mark.browser
def test_lazy_viewer_reconstructs_all_pages(
    fixture_server: FixtureServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOC_DL_STATE_DIR", str(tmp_path / "state"))
    result = DownloadEngine(quiet_sink()).download(
        DownloadRequest(
            url=fixture_server.url("/site/lazy-viewer.html"),
            output=tmp_path / "output",
            timeout_seconds=60,
        )
    )
    assert result.provenance == Provenance.RECONSTRUCTED
    assert result.page_count == 6
    assert result.path.is_file()
    assert_image_backed_pages(result.path, 6)


@pytest.mark.browser
def test_zero_byte_attachment_does_not_block_reconstruction(
    fixture_server: FixtureServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOC_DL_STATE_DIR", str(tmp_path / "state"))
    result = DownloadEngine(quiet_sink()).download(
        DownloadRequest(
            url=fixture_server.url("/site/false-attachment-viewer.html"),
            output=tmp_path / "output",
            timeout_seconds=60,
        )
    )
    assert result.provenance == Provenance.RECONSTRUCTED
    assert result.page_count == 6
    assert result.path.is_file()
    assert_image_backed_pages(result.path, 6)


@pytest.mark.browser
def test_blank_viewer_page_is_rejected(
    fixture_server: FixtureServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOC_DL_STATE_DIR", str(tmp_path / "state"))
    with pytest.raises(DocDlError) as raised:
        DownloadEngine(quiet_sink()).download(
            DownloadRequest(
                url=fixture_server.url("/site/blank-viewer.html"),
                output=tmp_path / "output",
                timeout_seconds=45,
            )
        )
    assert raised.value.identifier == "render_incomplete"


@pytest.mark.browser
def test_page_with_no_document_falls_back_to_a_page_capture(
    fixture_server: FixtureServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOC_DL_STATE_DIR", str(tmp_path / "state"))
    result = DownloadEngine(quiet_sink()).download(
        DownloadRequest(
            url=fixture_server.url("/site/article.html"),
            output=tmp_path / "output",
            timeout_seconds=45,
        )
    )
    assert result.provenance == Provenance.CAPTURED
    assert result.path.is_file()


@pytest.mark.browser
def test_original_only_skips_the_page_capture_fallback(
    fixture_server: FixtureServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOC_DL_STATE_DIR", str(tmp_path / "state"))
    with pytest.raises(DocDlError) as raised:
        DownloadEngine(quiet_sink()).download(
            DownloadRequest(
                url=fixture_server.url("/site/article.html"),
                output=tmp_path / "output",
                original_only=True,
                timeout_seconds=45,
            )
        )
    assert raised.value.identifier == "candidate_not_found"
