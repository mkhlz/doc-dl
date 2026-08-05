from __future__ import annotations

import io
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat, UnidentifiedImageError
from pypdf import PdfReader, PdfWriter

from doc_dl.errors import DocDlError
from doc_dl.events import EventSink
from doc_dl.models import Provenance
from doc_dl.providers.base import Provider
from doc_dl.verify import verify_document

_CAPTURE_VIEWPORT_MARGIN = 128
_MAX_CAPTURE_VIEWPORT_HEIGHT = 16_384


@dataclass(frozen=True, slots=True)
class RenderedArtifact:
    path: Path
    provenance: Provenance
    page_count: int


class PdfRenderer:
    def __init__(self, sink: EventSink) -> None:
        self.sink = sink

    def render(
        self,
        page: Any,
        provider: Provider,
        *,
        timeout_ms: float,
    ) -> RenderedArtifact:
        output = self._temporary_pdf()
        try:
            if provider.name != "generic":
                page_numbers = provider.render_page_numbers(page)
                if page_numbers:
                    count = self._render_provider_pages(
                        page,
                        provider,
                        page_numbers,
                        output,
                        timeout_ms,
                    )
                    return RenderedArtifact(output, Provenance.RECONSTRUCTED, count)

            selector = self._generic_page_selector(page)
            if selector:
                count = self._render_generic_pages(page, selector, output, timeout_ms)
                return RenderedArtifact(output, Provenance.RECONSTRUCTED, count)
        except Exception:
            output.unlink(missing_ok=True)
            raise

        output.unlink(missing_ok=True)
        raise DocDlError(
            "candidate_not_found",
            "The browser page did not expose document page containers for reconstruction",
        )

    def _render_provider_pages(
        self,
        page: Any,
        provider: Provider,
        page_numbers: list[int],
        output: Path,
        timeout_ms: float,
    ) -> int:
        writer = PdfWriter()
        with tempfile.TemporaryDirectory(prefix="doc-dl-page-spool-") as spool:
            spool_path = Path(spool)
            for index, page_number in enumerate(page_numbers, start=1):
                self.sink.emit(
                    "download_progress",
                    message=f"Reconstructing page {index}/{len(page_numbers)}",
                    downloaded=index - 1,
                    total=len(page_numbers),
                    unit="pages",
                )
                selector = provider.load_render_page(page, page_number, timeout_ms)
                if not selector:
                    raise DocDlError(
                        "render_incomplete",
                        f"Provider did not expose a selector for page {page_number}",
                    )
                self.sink.emit(
                    "download_progress",
                    message=f"Capturing page {index}/{len(page_numbers)}",
                    downloaded=index - 1,
                    total=len(page_numbers),
                    unit="pages",
                )
                page_file = spool_path / f"page-{index:06d}.pdf"
                try:
                    self._capture_element_pdf(
                        page,
                        page.locator(selector).first,
                        page_file,
                        timeout_ms,
                    )
                    self._append_single_page(writer, page_file, page_number)
                    self.sink.emit(
                        "download_progress",
                        message=f"Completed page {index}/{len(page_numbers)}",
                        downloaded=index,
                        total=len(page_numbers),
                        unit="pages",
                    )
                finally:
                    provider.release_render_page(page, page_number)
            self._write_merged(writer, output)

        verify_document(output, media_type_hint="application/pdf", expected_pages=len(page_numbers))
        return len(page_numbers)

    def _render_generic_pages(
        self,
        page: Any,
        selector: str,
        output: Path,
        timeout_ms: float,
    ) -> int:
        locator = page.locator(selector)
        count = locator.count()
        if count < 1:
            raise DocDlError("candidate_not_found", "No generic viewer pages were found")

        writer = PdfWriter()
        with tempfile.TemporaryDirectory(prefix="doc-dl-page-spool-") as spool:
            spool_path = Path(spool)
            for index in range(count):
                self.sink.emit(
                    "download_progress",
                    message=f"Reconstructing page {index + 1}/{count}",
                    downloaded=index,
                    total=count,
                    unit="pages",
                )
                target = locator.nth(index)
                page_file = spool_path / f"page-{index + 1:06d}.pdf"
                self._capture_element_pdf(page, target, page_file, timeout_ms)
                self._append_single_page(writer, page_file, index + 1)
            self._write_merged(writer, output)

        verify_document(output, media_type_hint="application/pdf", expected_pages=count)
        return count

    def _capture_element_pdf(
        self,
        page: Any,
        target: Any,
        output: Path,
        timeout_ms: float,
    ) -> None:
        target.wait_for(state="attached", timeout=timeout_ms)
        page.emulate_media(media="screen")
        original_viewport = self._viewport_size(page)
        try:
            self._fit_viewport_to_target(page, target, original_viewport)
            target.scroll_into_view_if_needed(timeout=timeout_ms)
            self._wait_for_images(target, timeout_ms)
            page.evaluate(
                """
                () => new Promise((resolve) => {
                  requestAnimationFrame(() => requestAnimationFrame(resolve));
                })
                """
            )
            png = target.screenshot(
                type="png",
                animations="disabled",
                caret="hide",
                scale="device",
                timeout=timeout_ms,
            )
        except DocDlError:
            raise
        except Exception as exc:
            raise DocDlError(
                "browser_failed",
                "Chromium could not capture a viewer page",
                detail=str(exc),
            ) from exc
        finally:
            if original_viewport is not None and self._viewport_size(page) != original_viewport:
                page.set_viewport_size(original_viewport)
        self._write_screenshot_pdf(png, output)

    @staticmethod
    def _viewport_size(page: Any) -> dict[str, int] | None:
        viewport = page.viewport_size
        if not viewport:
            return None
        return {
            "width": int(viewport["width"]),
            "height": int(viewport["height"]),
        }

    @staticmethod
    def _fit_viewport_to_target(
        page: Any,
        target: Any,
        original_viewport: dict[str, int] | None,
    ) -> None:
        if original_viewport is None:
            return

        current_height = original_viewport["height"]
        for _ in range(3):
            box = target.bounding_box()
            if box is None:
                raise DocDlError(
                    "render_incomplete",
                    "A viewer page has no measurable dimensions",
                )
            required_height = max(
                original_viewport["height"],
                math.ceil(float(box["height"])) + _CAPTURE_VIEWPORT_MARGIN,
            )
            if required_height <= current_height:
                return
            if required_height > _MAX_CAPTURE_VIEWPORT_HEIGHT:
                raise DocDlError(
                    "render_incomplete",
                    "A viewer page exceeds Chromium's safe capture height",
                    detail=(
                        f"Required viewport height {required_height}px exceeds "
                        f"the {_MAX_CAPTURE_VIEWPORT_HEIGHT}px limit."
                    ),
                )
            page.set_viewport_size(
                {
                    "width": original_viewport["width"],
                    "height": required_height,
                }
            )
            current_height = required_height

        box = target.bounding_box()
        target_exceeds_viewport = box is not None and (
            math.ceil(float(box["height"])) + _CAPTURE_VIEWPORT_MARGIN > current_height
        )
        if box is None or target_exceeds_viewport:
            raise DocDlError(
                "render_incomplete",
                "A viewer page did not stabilize within the capture viewport",
            )

    @staticmethod
    def _write_screenshot_pdf(png: bytes, output: Path) -> None:
        try:
            with Image.open(io.BytesIO(png)) as source:
                source.load()
                if source.width < 10 or source.height < 10:
                    raise DocDlError(
                        "render_incomplete",
                        "A viewer page has invalid dimensions",
                    )

                thumbnail = source.convert("RGB")
                thumbnail.thumbnail((256, 256), Image.Resampling.LANCZOS)
                gray = thumbnail.convert("L")
                histogram = gray.histogram()
                pixel_count = max(1, thumbnail.width * thumbnail.height)
                nonwhite_ratio = sum(histogram[:245]) / pixel_count
                brightness_stddev = float(ImageStat.Stat(gray).stddev[0])
                brightness_mean = float(ImageStat.Stat(gray).mean[0])
                if nonwhite_ratio < 0.001 and brightness_stddev < 0.5 and brightness_mean > 245:
                    raise DocDlError(
                        "render_incomplete",
                        "A captured viewer page is visually blank",
                    )

                if "A" in source.getbands():
                    image = Image.new("RGB", source.size, "white")
                    image.paste(source.convert("RGB"), mask=source.getchannel("A"))
                else:
                    image = source.convert("RGB")
                image.save(
                    output,
                    format="PDF",
                    resolution=96.0,
                    quality=92,
                    optimize=True,
                )
        except DocDlError:
            raise
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            raise DocDlError(
                "render_incomplete",
                "A captured viewer page could not be encoded as PDF",
                detail=str(exc),
            ) from exc

    @staticmethod
    def _wait_for_images(locator: Any, timeout_ms: float) -> None:
        try:
            locator.evaluate(
                """
                async (element, timeoutMs) => {
                  const started = Date.now();
                  while (Date.now() - started < timeoutMs) {
                    const images = Array.from(element.querySelectorAll('img'));
                    if (images.every((image) => image.complete && image.naturalWidth > 0)) return;
                    await new Promise((resolve) => setTimeout(resolve, 100));
                  }
                  throw new Error('viewer images did not finish loading');
                }
                """,
                timeout_ms,
            )
        except Exception as exc:
            raise DocDlError(
                "render_incomplete",
                "A viewer page image did not finish loading",
                detail=str(exc),
            ) from exc

    @staticmethod
    def _generic_page_selector(page: Any) -> str | None:
        selectors = (
            "[data-doc-page]",
            "[data-page-number].page",
            ".pdf-page[data-page-number]",
            ".document-page[data-page]",
        )
        for selector in selectors:
            if page.locator(selector).count() > 0:
                return selector
        return None

    @staticmethod
    def _append_single_page(writer: PdfWriter, path: Path, page_number: int) -> None:
        try:
            reader = PdfReader(io.BytesIO(path.read_bytes()), strict=False)
        except Exception as exc:
            raise DocDlError(
                "corrupt_document",
                f"Rendered page {page_number} is not a valid PDF",
                detail=str(exc),
            ) from exc
        if len(reader.pages) != 1:
            raise DocDlError(
                "render_incomplete",
                f"Rendered document page {page_number} produced {len(reader.pages)} PDF sheets",
            )
        if len(reader.pages[0].images) < 1:
            raise DocDlError(
                "render_incomplete",
                f"Rendered document page {page_number} contains no visible image content",
            )
        writer.add_page(reader.pages[0])

    @staticmethod
    def _write_merged(writer: PdfWriter, output: Path) -> None:
        try:
            with output.open("wb") as handle:
                writer.write(handle)
        except OSError as exc:
            raise DocDlError(
                "filesystem_failure",
                "The reconstructed PDF could not be written",
                detail=str(exc),
            ) from exc

    @staticmethod
    def _temporary_pdf() -> Path:
        with tempfile.NamedTemporaryFile(
            prefix="doc-dl-render-", suffix=".pdf", delete=False
        ) as handle:
            path = Path(handle.name)
        path.unlink(missing_ok=True)
        return path
