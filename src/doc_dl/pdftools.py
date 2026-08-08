from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from doc_dl.errors import DocDlError


def extract_pdf_pages(source: Path, page_range: tuple[int, int], output: Path) -> int:
    """Copy a page range (1-indexed, inclusive) out of `source` into a new
    PDF at `output` -- grabbing one chapter out of a long book without
    re-uploading the whole thing. Returns the number of pages copied."""
    try:
        reader = PdfReader(str(source), strict=False)
        total_pages = len(reader.pages)
    except (PdfReadError, OSError, ValueError) as exc:
        raise DocDlError(
            "corrupt_document", "The source file is not a readable PDF", detail=str(exc)
        ) from exc

    start, end = page_range
    if start < 1 or end > total_pages or start > end:
        raise DocDlError(
            "invalid_arguments",
            f"--pages must be within 1-{total_pages} for this {total_pages}-page PDF",
        )

    writer = PdfWriter()
    for index in range(start - 1, end):
        writer.add_page(reader.pages[index])

    try:
        with output.open("wb") as handle:
            writer.write(handle)
    except OSError as exc:
        raise DocDlError(
            "filesystem_failure", "The extracted PDF could not be written", detail=str(exc)
        ) from exc
    return end - start + 1
