from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from doc_dl.errors import DocDlError
from doc_dl.pdftools import extract_pdf_pages


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "book.pdf"
    writer = PdfWriter()
    for _ in range(10):
        writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def test_extract_pdf_pages_copies_the_requested_range(sample_pdf: Path, tmp_path: Path) -> None:
    output = tmp_path / "chapter.pdf"
    count = extract_pdf_pages(sample_pdf, (3, 5), output)
    assert count == 3
    reader = PdfReader(str(output))
    assert len(reader.pages) == 3


def test_extract_pdf_pages_rejects_a_range_beyond_the_document(
    sample_pdf: Path, tmp_path: Path
) -> None:
    with pytest.raises(DocDlError) as raised:
        extract_pdf_pages(sample_pdf, (8, 20), tmp_path / "out.pdf")
    assert raised.value.identifier == "invalid_arguments"


def test_extract_pdf_pages_rejects_a_non_pdf_file(tmp_path: Path) -> None:
    bogus = tmp_path / "not-a-pdf.pdf"
    bogus.write_text("this is not a PDF")
    with pytest.raises(DocDlError) as raised:
        extract_pdf_pages(bogus, (1, 1), tmp_path / "out.pdf")
    assert raised.value.identifier == "corrupt_document"


def test_extract_pages_cli_end_to_end(sample_pdf: Path, tmp_path: Path) -> None:
    from doc_dl.cli import run

    output = tmp_path / "chapter.pdf"
    exit_code = run(
        [
            "extract-pages",
            str(sample_pdf),
            "--pages",
            "2-4",
            "-o",
            str(output),
            "--quiet",
        ]
    )
    assert exit_code == 0
    reader = PdfReader(str(output))
    assert len(reader.pages) == 3


def test_extract_pages_requires_an_existing_file(tmp_path: Path) -> None:
    from doc_dl.cli import run

    exit_code = run(["extract-pages", str(tmp_path / "missing.pdf"), "--pages", "1-2", "--quiet"])
    assert exit_code == 2
