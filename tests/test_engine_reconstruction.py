from __future__ import annotations

import io

import pytest

from doc_dl.engine import DownloadEngine
from doc_dl.errors import DocDlError
from doc_dl.events import EventSink
from doc_dl.models import DownloadRequest, ImagePageSet
from doc_dl.providers.base import Provider
from doc_dl.providers.registry import ProviderRegistry
from tests.fixture_server import FixtureServer


def quiet_sink() -> EventSink:
    return EventSink(quiet=True, stream=io.StringIO(), error_stream=io.StringIO())


class _ImageSetProvider(Provider):
    """A provider that always claims it can rebuild the document from a fixed
    list of page image URLs, one of which is permanently unavailable."""

    name = "generic"
    supports_render = True

    def __init__(self, urls: list[str]) -> None:
        self._urls = urls

    def match(self, url: str) -> int:
        del url
        return 1

    def image_pages_from_html(self, html: str, url: str) -> ImagePageSet:
        del html, url
        return ImagePageSet(title="Fixture Deck", image_urls=self._urls)


def test_reconstruction_failure_is_surfaced_not_masked_by_fallbacks(
    fixture_server: FixtureServer,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOC_DL_STATE_DIR", str(tmp_path / "state"))
    provider = _ImageSetProvider(
        [fixture_server.url(f"/imageset/gap/{n}.jpg") for n in range(1, 6)]
    )
    engine = DownloadEngine(quiet_sink(), registry=ProviderRegistry([provider]))

    with pytest.raises(DocDlError) as raised:
        engine.download(
            DownloadRequest(
                url=fixture_server.url("/site/false-attachment-viewer.html"),
                output=tmp_path / "output",
                browser_enabled=False,
                timeout_seconds=30,
            )
        )

    # The real cause (a page missing from the document) must survive instead
    # of being replaced by a later fallback's generic complaint.
    assert raised.value.identifier == "render_incomplete"
    assert "Page 2 of 5" in raised.value.message
