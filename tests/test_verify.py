from __future__ import annotations

from pathlib import Path

import pytest

from doc_dl.errors import DocDlError
from doc_dl.verify import (
    ensure_document_extension,
    response_looks_document_like,
    verify_document,
)
from tests.fixture_server import DOCX_BYTES, PDF_BYTES


def test_extension_is_corrected_when_it_contradicts_the_content() -> None:
    # SlideShare titles keep the uploader's original filename, so a
    # reconstructed PDF would otherwise be saved as an unopenable ".pptx".
    assert (
        ensure_document_extension("ISLAMIC-PHILOSOPHY-2-pptx.pptx", "application/pdf")
        == "ISLAMIC-PHILOSOPHY-2-pptx.pdf"
    )


def test_matching_extension_is_left_alone() -> None:
    assert ensure_document_extension("report.pdf", "application/pdf") == "report.pdf"
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert ensure_document_extension("notes.docx", docx) == "notes.docx"


def test_missing_or_placeholder_extension_is_filled_in() -> None:
    assert ensure_document_extension("document", "application/pdf") == "document.pdf"
    assert ensure_document_extension("download.bin", "application/pdf") == "download.pdf"


def test_unrelated_suffix_is_appended_rather_than_replaced() -> None:
    # "2024.1" is part of the name, not a file type, so it must survive.
    assert ensure_document_extension("report-2024.1", "application/pdf") == "report-2024.1.pdf"


def test_plain_text_telemetry_response_is_not_document_like() -> None:
    # Cloudflare's /cdn-cgi/trace endpoint returns text/plain with no
    # attachment disposition; it must never be mistaken for the document.
    assert not response_looks_document_like(
        url="https://www.slideshare.net/cdn-cgi/trace",
        media_type="text/plain",
        content_disposition=None,
    )


def test_plain_text_with_attachment_disposition_is_document_like() -> None:
    assert response_looks_document_like(
        url="https://example.com/export",
        media_type="text/plain",
        content_disposition='attachment; filename="notes.txt"',
    )


def test_plain_text_with_document_extension_is_document_like() -> None:
    assert response_looks_document_like(
        url="https://example.com/notes.txt",
        media_type="text/plain",
        content_disposition=None,
    )


def test_binary_document_type_still_qualifies_on_media_type_alone() -> None:
    assert response_looks_document_like(
        url="https://example.com/stream",
        media_type="application/pdf",
        content_disposition=None,
    )


def test_verifies_pdf_and_page_count(tmp_path: Path) -> None:
    path = tmp_path / "document.pdf"
    path.write_bytes(PDF_BYTES)
    result = verify_document(path, expected_pages=1)
    assert result.media_type == "application/pdf"
    assert result.page_count == 1


def test_verifies_docx_container(tmp_path: Path) -> None:
    path = tmp_path / "document.bin"
    path.write_bytes(DOCX_BYTES)
    result = verify_document(path)
    assert result.media_type.endswith("wordprocessingml.document")


def test_rejects_html_masquerading_as_pdf(tmp_path: Path) -> None:
    path = tmp_path / "fake.pdf"
    path.write_text("<!doctype html><html><body>login</body></html>", encoding="utf-8")
    with pytest.raises(DocDlError) as raised:
        verify_document(path, media_type_hint="application/pdf")
    assert raised.value.identifier == "unexpected_content"


def test_rejects_corrupt_pdf(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"%PDF-1.7\nnot valid")
    with pytest.raises(DocDlError) as raised:
        verify_document(path)
    assert raised.value.identifier == "corrupt_document"
