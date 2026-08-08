from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from doc_dl.engine import DownloadEngine
from doc_dl.errors import DocDlError
from doc_dl.events import EventSink
from doc_dl.http import HttpDownloader, RetrievedDocument
from doc_dl.models import DocumentCandidate, DownloadRequest, Provenance
from tests.fixture_server import FixtureServer


def quiet_sink() -> EventSink:
    return EventSink(quiet=True, stream=io.StringIO(), error_stream=io.StringIO())


@pytest.mark.parametrize(
    ("path", "expected_suffix", "expected_media"),
    [
        ("/files/sample.pdf", ".pdf", "application/pdf"),
        (
            "/opaque/document/42",
            ".docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("/redirect/pdf", ".pdf", "application/pdf"),
    ],
)
def test_direct_downloads(
    fixture_server: FixtureServer,
    tmp_path: Path,
    path: str,
    expected_suffix: str,
    expected_media: str,
) -> None:
    candidate = DocumentCandidate(url=fixture_server.url(path), strategy="direct-http")
    result = HttpDownloader(quiet_sink(), retry_base_delay=0).fetch(
        candidate,
        DownloadRequest(url=candidate.url, output=tmp_path, browser_enabled=False),
    )
    assert isinstance(result, RetrievedDocument)
    assert result.path.suffix == expected_suffix
    assert result.media_type == expected_media
    assert result.path.is_file()


@pytest.mark.parametrize("path", ["/site/static-link.html", "/site/jsonld-link.html"])
def test_static_landing_page_discovery(
    fixture_server: FixtureServer,
    tmp_path: Path,
    path: str,
) -> None:
    request = DownloadRequest(
        url=fixture_server.url(path),
        output=tmp_path,
        browser_enabled=False,
    )
    result = DownloadEngine(quiet_sink()).download(request)
    assert result.provenance == Provenance.ORIGINAL
    assert result.media_type == "application/pdf"
    assert result.path.is_file()


def test_retry_after_then_success(fixture_server: FixtureServer, tmp_path: Path) -> None:
    url = fixture_server.url("/retry/rate-limit")
    result = HttpDownloader(quiet_sink(), retry_base_delay=0).fetch(
        DocumentCandidate(url=url, strategy="direct-http"),
        DownloadRequest(url=url, output=tmp_path, retries=2),
    )
    assert isinstance(result, RetrievedDocument)
    assert fixture_server.state.counts["/retry/rate-limit"] == 2


def test_interrupted_transfer_resumes_safely(
    fixture_server: FixtureServer,
    tmp_path: Path,
) -> None:
    url = fixture_server.url("/resume/pdf")
    result = HttpDownloader(quiet_sink(), retry_base_delay=0).fetch(
        DocumentCandidate(url=url, strategy="direct-http"),
        DownloadRequest(url=url, output=tmp_path, retries=2),
    )
    assert isinstance(result, RetrievedDocument)
    assert result.resumed is True
    assert result.page_count == 1
    assert fixture_server.state.counts["/resume/pdf"] == 2


def test_rejects_html_advertised_as_pdf(fixture_server: FixtureServer, tmp_path: Path) -> None:
    url = fixture_server.url("/errors/fake.pdf")
    with pytest.raises(DocDlError) as raised:
        HttpDownloader(quiet_sink(), retry_base_delay=0).fetch(
            DocumentCandidate(url=url, strategy="direct-http", confidence=90),
            DownloadRequest(url=url, output=tmp_path),
        )
    assert raised.value.identifier == "unexpected_content"


def test_rejects_corrupt_pdf_response(fixture_server: FixtureServer, tmp_path: Path) -> None:
    url = fixture_server.url("/errors/corrupt.pdf")
    with pytest.raises(DocDlError) as raised:
        HttpDownloader(quiet_sink(), retry_base_delay=0).fetch(
            DocumentCandidate(url=url, strategy="direct-http", confidence=90),
            DownloadRequest(url=url, output=tmp_path),
        )
    assert raised.value.identifier == "corrupt_document"


def test_accepts_text_attachment_with_generic_media_type(
    fixture_server: FixtureServer,
    tmp_path: Path,
) -> None:
    url = fixture_server.url("/files/attachment-text")
    result = HttpDownloader(quiet_sink(), retry_base_delay=0).fetch(
        DocumentCandidate(url=url, strategy="browser-network", confidence=95),
        DownloadRequest(url=url, output=tmp_path),
    )
    assert isinstance(result, RetrievedDocument)
    assert result.path.suffix == ".txt"
    assert result.media_type == "text/plain"


def test_write_metadata_records_to_the_central_history_log_not_a_sidecar(
    fixture_server: FixtureServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    monkeypatch.setenv("DOC_DL_STATE_DIR", str(state_dir))
    output_dir = tmp_path / "output"
    result = DownloadEngine(quiet_sink()).download(
        DownloadRequest(
            url=fixture_server.url("/files/sample.pdf"),
            output=output_dir,
            write_metadata=True,
        )
    )
    # Pasting a link and getting a file back stays exactly that: the
    # download folder holds only the file, never a .doc-dl.json alongside it.
    assert list(output_dir.iterdir()) == [result.path]

    entries = (state_dir / "history.jsonl").read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(entries[-1])
    assert payload["path"] == str(result.path)
    assert payload["provenance"] == "original"
