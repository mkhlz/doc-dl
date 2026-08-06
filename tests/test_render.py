from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader

from doc_dl.errors import DocDlError
from doc_dl.render import PdfRenderer


class FakePage:
    def __init__(self, viewport: dict[str, int]) -> None:
        self.viewport_size = dict(viewport)
        self.viewport_changes: list[dict[str, int]] = []
        self.waited_for_timeout = 0

    def set_viewport_size(self, viewport: dict[str, int]) -> None:
        self.viewport_size = dict(viewport)
        self.viewport_changes.append(dict(viewport))

    def wait_for_timeout(self, _timeout: float) -> None:
        self.waited_for_timeout += 1


class FakeTarget:
    def __init__(self, heights: list[float | None]) -> None:
        self.heights = list(heights)
        self.index = 0

    def bounding_box(self) -> dict[str, float] | None:
        index = min(self.index, len(self.heights) - 1)
        self.index += 1
        height = self.heights[index]
        if height is None:
            return None
        return {"x": 0.0, "y": 0.0, "width": 800.0, "height": height}


class FakeCapturePage(FakePage):
    def emulate_media(self, *, media: str) -> None:
        assert media == "screen"

    def evaluate(self, _script: str) -> None:
        return None


class FakeCaptureTarget(FakeTarget):
    def wait_for(self, *, state: str, timeout: float) -> None:
        assert state == "attached"
        assert timeout > 0

    def scroll_into_view_if_needed(self, *, timeout: float) -> None:
        assert timeout > 0

    def evaluate(self, _script: str, _timeout: float) -> None:
        return None

    def screenshot(self, **_options: object) -> bytes:
        return make_png(visible_content=True)


def make_png(*, visible_content: bool) -> bytes:
    image = Image.new("RGB", (800, 1000), "white")
    if visible_content:
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 100, 720, 240), fill="#17365d")
        draw.rectangle((80, 320, 640, 350), fill="#d24726")
        draw.line((80, 430, 720, 430), fill="#222222", width=8)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_screenshot_pdf_rejects_visually_blank_page(tmp_path: Path) -> None:
    with pytest.raises(DocDlError) as raised:
        PdfRenderer._write_screenshot_pdf(
            make_png(visible_content=False),
            tmp_path / "blank.pdf",
        )
    assert raised.value.identifier == "render_incomplete"
    assert "visually blank" in raised.value.message


def test_screenshot_pdf_contains_visible_image_page(tmp_path: Path) -> None:
    output = tmp_path / "content.pdf"
    PdfRenderer._write_screenshot_pdf(make_png(visible_content=True), output)
    reader = PdfReader(str(output), strict=False)
    assert len(reader.pages) == 1
    assert len(reader.pages[0].images) == 1


def test_capture_viewport_grows_until_tall_page_fits() -> None:
    page = FakePage({"width": 1440, "height": 1000})
    target = FakeTarget([1412.4, 1412.4])
    original = PdfRenderer._viewport_size(page)

    PdfRenderer._fit_viewport_to_target(page, target, original, 5_000)

    assert original == {"width": 1440, "height": 1000}
    assert page.viewport_size == {"width": 1440, "height": 1541}
    assert page.viewport_changes == [{"width": 1440, "height": 1541}]


def test_capture_viewport_handles_responsive_height_change() -> None:
    page = FakePage({"width": 1440, "height": 1000})
    target = FakeTarget([1200.0, 1500.0, 1500.0])

    PdfRenderer._fit_viewport_to_target(
        page,
        target,
        PdfRenderer._viewport_size(page),
        5_000,
    )

    assert page.viewport_size == {"width": 1440, "height": 1628}
    assert page.viewport_changes == [
        {"width": 1440, "height": 1328},
        {"width": 1440, "height": 1628},
    ]


def test_capture_viewport_rejects_unsafe_height() -> None:
    page = FakePage({"width": 1440, "height": 1000})
    target = FakeTarget([20_000.0])

    with pytest.raises(DocDlError) as raised:
        PdfRenderer._fit_viewport_to_target(
            page,
            target,
            PdfRenderer._viewport_size(page),
            5_000,
        )

    assert raised.value.identifier == "render_incomplete"
    assert "safe capture height" in raised.value.message


def test_measure_target_tolerates_a_brief_unmeasurable_window() -> None:
    page = FakePage({"width": 1440, "height": 1000})
    target = FakeTarget([None, None, 1412.4])

    box = PdfRenderer._measure_target(page, target, 5_000)

    assert box["height"] == 1412.4
    assert page.waited_for_timeout == 2


def test_measure_target_raises_when_never_measurable() -> None:
    page = FakePage({"width": 1440, "height": 1000})
    target = FakeTarget([None])

    with pytest.raises(DocDlError) as raised:
        PdfRenderer._measure_target(page, target, 100)

    assert raised.value.identifier == "render_incomplete"
    assert "no measurable dimensions" in raised.value.message


def test_capture_scrolls_into_view_before_measuring_dimensions(tmp_path: Path) -> None:
    calls: list[str] = []

    class OrderTrackingTarget(FakeCaptureTarget):
        def scroll_into_view_if_needed(self, *, timeout: float) -> None:
            calls.append("scroll")
            super().scroll_into_view_if_needed(timeout=timeout)

        def bounding_box(self) -> dict[str, float] | None:
            calls.append("measure")
            return super().bounding_box()

    page = FakeCapturePage({"width": 1440, "height": 1000})
    target = OrderTrackingTarget([1412.4, 1412.4])

    PdfRenderer._capture_element_pdf(
        PdfRenderer.__new__(PdfRenderer),
        page,
        target,
        tmp_path / "captured.pdf",
        5_000,
    )

    assert calls[0] == "scroll"
    assert calls.index("scroll") < calls.index("measure")


def test_element_capture_restores_original_viewport(tmp_path: Path) -> None:
    page = FakeCapturePage({"width": 1440, "height": 1000})
    target = FakeCaptureTarget([1412.4, 1412.4])

    PdfRenderer._capture_element_pdf(
        PdfRenderer.__new__(PdfRenderer),
        page,
        target,
        tmp_path / "captured.pdf",
        5_000,
    )

    assert page.viewport_size == {"width": 1440, "height": 1000}
    assert page.viewport_changes == [
        {"width": 1440, "height": 1541},
        {"width": 1440, "height": 1000},
    ]
