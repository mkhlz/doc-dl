from __future__ import annotations

import tempfile
import time
from pathlib import Path

import httpx
from pypdf import PdfWriter

from doc_dl.errors import DocDlError
from doc_dl.events import EventSink
from doc_dl.models import ImagePageSet
from doc_dl.render import (
    append_single_page,
    temporary_pdf_path,
    write_image_page_pdf,
    write_merged_pdf,
)
from doc_dl.verify import verify_document


class ImageSetReconstructor:
    """Assembles a PDF from a provider's already-known list of per-page
    image URLs, fetched directly over HTTP with no browser involved."""

    def __init__(self, sink: EventSink, *, user_agent: str = "doc-dl/0.1") -> None:
        self.sink = sink
        self.user_agent = user_agent

    def reconstruct(self, image_set: ImagePageSet, timeout_seconds: float) -> tuple[Path, int]:
        total = len(image_set.image_urls)
        deadline = time.monotonic() + timeout_seconds
        output = temporary_pdf_path()
        writer = PdfWriter()
        try:
            timeout = httpx.Timeout(connect=20.0, read=60.0, write=60.0, pool=20.0)
            with (
                tempfile.TemporaryDirectory(prefix="doc-dl-page-spool-") as spool,
                httpx.Client(
                    follow_redirects=True,
                    timeout=timeout,
                    headers={"User-Agent": self.user_agent},
                ) as client,
            ):
                spool_path = Path(spool)
                for index, url in enumerate(image_set.image_urls, start=1):
                    if time.monotonic() >= deadline:
                        raise DocDlError(
                            "operation_timeout",
                            "The reconstruction deadline expired",
                        )
                    self.sink.emit(
                        "download_progress",
                        downloaded=index - 1,
                        total=total,
                        unit="pages",
                    )
                    image_bytes = self._fetch(client, url)
                    page_file = spool_path / f"page-{index:06d}.pdf"
                    write_image_page_pdf(image_bytes, page_file)
                    append_single_page(writer, page_file, index)
                self.sink.emit("download_progress", downloaded=total, total=total, unit="pages")
                write_merged_pdf(writer, output)
        except Exception:
            output.unlink(missing_ok=True)
            raise

        verify_document(output, media_type_hint="application/pdf", expected_pages=total)
        return output, total

    @staticmethod
    def _fetch(client: httpx.Client, url: str) -> bytes:
        try:
            response = client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DocDlError(
                "network_failure",
                "A reconstructed page image could not be downloaded",
                detail=str(exc),
            ) from exc
        return response.content
