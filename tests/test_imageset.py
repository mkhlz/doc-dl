from __future__ import annotations

import io

import pytest
from pypdf import PdfReader

from doc_dl.errors import DocDlError
from doc_dl.events import EventSink
from doc_dl.imageset import ImageSetReconstructor
from doc_dl.models import ImagePageSet
from tests.fixture_server import FixtureServer


def quiet_sink() -> EventSink:
    return EventSink(quiet=True, stream=io.StringIO(), error_stream=io.StringIO())


def test_reconstruct_fetches_each_page_and_merges_into_one_pdf(
    fixture_server: FixtureServer,
) -> None:
    image_set = ImagePageSet(
        title="Fixture Deck",
        image_urls=[
            fixture_server.url("/imageset/pages/1.jpg"),
            fixture_server.url("/imageset/pages/2.jpg"),
            fixture_server.url("/imageset/pages/3.jpg"),
        ],
    )
    reconstructor = ImageSetReconstructor(quiet_sink())

    output, page_count = reconstructor.reconstruct(image_set, timeout_seconds=30)

    assert page_count == 3
    reader = PdfReader(str(output), strict=False)
    assert len(reader.pages) == 3
    output.unlink()


def test_reconstruct_raises_and_cleans_up_on_missing_page(
    fixture_server: FixtureServer,
) -> None:
    image_set = ImagePageSet(
        title="Fixture Deck",
        image_urls=[
            fixture_server.url("/imageset/pages/1.jpg"),
            fixture_server.url("/imageset/missing-page.jpg"),
        ],
    )
    reconstructor = ImageSetReconstructor(quiet_sink())

    with pytest.raises(DocDlError) as raised:
        reconstructor.reconstruct(image_set, timeout_seconds=30)

    assert raised.value.identifier == "network_failure"
