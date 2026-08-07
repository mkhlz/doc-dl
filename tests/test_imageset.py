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
        image_urls=[fixture_server.url(f"/imageset/gap/{n}.jpg") for n in range(1, 6)],
    )
    reconstructor = ImageSetReconstructor(quiet_sink())

    with pytest.raises(DocDlError) as raised:
        reconstructor.reconstruct(image_set, timeout_seconds=30)

    assert raised.value.identifier == "render_incomplete"


def test_reconstruct_retries_a_transient_page_failure_and_succeeds(
    fixture_server: FixtureServer,
) -> None:
    image_set = ImagePageSet(
        title="Fixture Deck",
        image_urls=[
            fixture_server.url("/imageset/pages/1.jpg"),
            fixture_server.url("/imageset/flaky-page.jpg"),
        ],
    )
    reconstructor = ImageSetReconstructor(quiet_sink())

    output, page_count = reconstructor.reconstruct(image_set, timeout_seconds=30)

    assert page_count == 2
    assert fixture_server.state.counts["/imageset/flaky-page.jpg"] == 2
    output.unlink()


def test_permanent_page_failure_is_not_retried(fixture_server: FixtureServer) -> None:
    image_set = ImagePageSet(
        title="Fixture Deck",
        image_urls=[fixture_server.url("/imageset/missing-page.jpg")],
    )
    reconstructor = ImageSetReconstructor(quiet_sink())

    with pytest.raises(DocDlError):
        reconstructor.reconstruct(image_set, timeout_seconds=30)

    assert fixture_server.state.counts["/imageset/missing-page.jpg"] == 1


def test_overannounced_trailing_pages_are_truncated_with_a_warning(
    fixture_server: FixtureServer,
) -> None:
    # The site claims 5 pages but only publishes 3, as SlideShare does for
    # some decks. The 3 real pages are worth more than a hard failure.
    stream = io.StringIO()
    sink = EventSink(stream=stream, error_stream=io.StringIO(), color=False)
    image_set = ImagePageSet(
        title="Fixture Deck",
        image_urls=[fixture_server.url(f"/imageset/truncated/{n}.jpg") for n in range(1, 6)],
    )

    output, page_count = ImageSetReconstructor(sink).reconstruct(image_set, timeout_seconds=30)

    assert page_count == 3
    reader = PdfReader(str(output), strict=False)
    assert len(reader.pages) == 3
    assert "announced 5 pages but only published 3" in stream.getvalue()
    output.unlink()


def test_missing_page_in_the_middle_is_a_hard_failure(
    fixture_server: FixtureServer,
) -> None:
    # Page 2 is absent but later pages exist, so this is a real hole rather
    # than a document that simply ends early: truncating would lose content.
    image_set = ImagePageSet(
        title="Fixture Deck",
        image_urls=[fixture_server.url(f"/imageset/gap/{n}.jpg") for n in range(1, 6)],
    )

    with pytest.raises(DocDlError) as raised:
        ImageSetReconstructor(quiet_sink()).reconstruct(image_set, timeout_seconds=30)

    assert raised.value.identifier == "render_incomplete"
    assert "missing from the middle" in raised.value.message


def test_too_few_available_pages_is_a_hard_failure(fixture_server: FixtureServer) -> None:
    # Only 3 of 20 announced pages exist: too far from reality to deliver.
    image_set = ImagePageSet(
        title="Fixture Deck",
        image_urls=[fixture_server.url(f"/imageset/truncated/{n}.jpg") for n in range(1, 21)],
    )

    with pytest.raises(DocDlError) as raised:
        ImageSetReconstructor(quiet_sink()).reconstruct(image_set, timeout_seconds=30)

    assert raised.value.identifier == "render_incomplete"
    assert "Only 3 of the 20 announced pages" in raised.value.message


def test_exhausted_retries_name_the_failing_page(fixture_server: FixtureServer) -> None:
    image_set = ImagePageSet(
        title="Fixture Deck",
        image_urls=[
            fixture_server.url("/imageset/pages/1.jpg"),
            fixture_server.url("/imageset/pages/2.jpg"),
            fixture_server.url("/imageset/always-unavailable.jpg"),
        ],
    )
    reconstructor = ImageSetReconstructor(quiet_sink())

    with pytest.raises(DocDlError) as raised:
        reconstructor.reconstruct(image_set, timeout_seconds=2)

    assert raised.value.identifier == "network_failure"
    assert "Page 3 of 3" in raised.value.message
