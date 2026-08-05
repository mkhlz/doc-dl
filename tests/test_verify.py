from __future__ import annotations

from pathlib import Path

import pytest

from doc_dl.errors import DocDlError
from doc_dl.verify import verify_document
from tests.fixture_server import DOCX_BYTES, PDF_BYTES


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
