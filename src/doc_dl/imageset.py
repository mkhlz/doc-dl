from __future__ import annotations

import random
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

# A single transient blip should never discard the pages already rebuilt, so
# each page image gets its own retries before the whole document is failed.
PAGE_FETCH_ATTEMPTS = 4
RETRY_BASE_DELAY = 0.5
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}

# Sites sometimes announce more pages than they actually publish, leaving the
# last few permanently absent. Delivering the pages that do exist beats
# delivering nothing, but only when most of the document survives; below this
# share, the announced count is too far from reality to trust.
MIN_AVAILABLE_FRACTION = 0.5


class _PageUnavailable(Exception):
    """A page image is permanently absent, as opposed to transiently failing."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


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
                available = total
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
                    try:
                        image_bytes = self._fetch(client, url, index, total, deadline)
                    except _PageUnavailable as exc:
                        available = self._resolve_missing_page(
                            client, image_set.image_urls, index, total, exc, deadline
                        )
                        break
                    page_file = spool_path / f"page-{index:06d}.pdf"
                    write_image_page_pdf(image_bytes, page_file)
                    append_single_page(writer, page_file, index)
                self.sink.emit(
                    "download_progress", downloaded=available, total=available, unit="pages"
                )
                write_merged_pdf(writer, output)
        except Exception:
            output.unlink(missing_ok=True)
            raise

        verify_document(output, media_type_hint="application/pdf", expected_pages=available)
        return output, available

    def _resolve_missing_page(
        self,
        client: httpx.Client,
        urls: list[str],
        index: int,
        total: int,
        exc: _PageUnavailable,
        deadline: float,
    ) -> int:
        """Decide whether a permanently absent page means the document simply
        ends early, or that a page is missing from the middle of it."""
        if index < total and self._page_exists(client, urls[-1], deadline):
            raise DocDlError(
                "render_incomplete",
                f"Page {index} of {total} is missing from the middle of the document",
                detail=exc.detail,
            )

        available = index - 1
        if available < 1 or available < total * MIN_AVAILABLE_FRACTION:
            raise DocDlError(
                "render_incomplete",
                f"Only {available} of the {total} announced pages could be downloaded",
                detail=exc.detail,
            )
        self.sink.emit(
            "warning",
            message=(
                f"Note: the site announced {total} pages but only published "
                f"{available}; saving the {available} pages that exist."
            ),
            announced=total,
            available=available,
        )
        return available

    @staticmethod
    def _page_exists(client: httpx.Client, url: str, deadline: float) -> bool:
        if time.monotonic() >= deadline:
            return False
        try:
            return client.head(url).status_code < 400
        except httpx.HTTPError:
            return False

    def _fetch(
        self,
        client: httpx.Client,
        url: str,
        page_number: int,
        total: int,
        deadline: float,
    ) -> bytes:
        last_detail = "no response"
        for attempt in range(PAGE_FETCH_ATTEMPTS):
            try:
                response = client.get(url)
            except httpx.HTTPError as exc:
                last_detail = str(exc)
            else:
                if response.status_code not in RETRYABLE_STATUS:
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        # A permanent status will not improve by asking again.
                        raise _PageUnavailable(str(exc)) from exc
                    return response.content
                last_detail = f"HTTP {response.status_code}"

            if attempt == PAGE_FETCH_ATTEMPTS - 1:
                break
            delay = min(RETRY_BASE_DELAY * (2**attempt) + random.uniform(0, 0.25), 10.0)
            if time.monotonic() + delay >= deadline:
                break
            self.sink.emit(
                "retry",
                message=(
                    f"Page {page_number} of {total} failed ({last_detail}); "
                    f"retrying in {delay:.2f} seconds"
                ),
                page=page_number,
                attempt=attempt + 1,
            )
            time.sleep(delay)

        raise DocDlError(
            "network_failure",
            (
                f"Page {page_number} of {total} could not be downloaded after "
                f"{PAGE_FETCH_ATTEMPTS} attempts"
            ),
            detail=last_detail,
            retryable=True,
        )
