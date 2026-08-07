from __future__ import annotations

from doc_dl.engine import DownloadEngine
from doc_dl.models import DocumentCandidate
from doc_dl.providers.generic import GenericProvider
from doc_dl.providers.scribd import ScribdProvider
from doc_dl.providers.slideshare import SlideShareProvider
from doc_dl.urls import registrable_domain, same_site


def candidate(url: str) -> DocumentCandidate:
    return DocumentCandidate(url=url, strategy="static-html")


def test_registrable_domain_ignores_subdomains() -> None:
    assert registrable_domain("www.slideshare.net") == "slideshare.net"
    assert registrable_domain("image.slidesharecdn.com") == "slidesharecdn.com"
    assert registrable_domain("slideshare.net") == "slideshare.net"


def test_same_site_matches_across_subdomains() -> None:
    assert same_site("www.slideshare.net", "slideshare.net")
    assert not same_site("hrmars.com", "www.slideshare.net")
    assert not same_site("", "www.slideshare.net")


def test_cited_pdf_on_another_site_is_not_treated_as_the_document() -> None:
    # A slide deck citing a paper must still download as the deck.
    cited = candidate("http://hrmars.com/hrmars_papers/Orientalists_and_Islam_Ramifications.pdf")
    owned, foreign = DownloadEngine._split_third_party_candidates(
        [cited],
        SlideShareProvider(),
        "https://www.slideshare.net/slideshow/a-deck/255323030",
    )

    assert owned == []
    assert foreign == [cited]


def test_same_site_and_cdn_candidates_are_kept() -> None:
    own_site = candidate("https://www.slideshare.net/download/a-deck.pptx")
    own_cdn = candidate("https://image.slidesharecdn.com/a-deck/file.pdf")
    owned, foreign = DownloadEngine._split_third_party_candidates(
        [own_site, own_cdn],
        SlideShareProvider(),
        "https://www.slideshare.net/slideshow/a-deck/255323030",
    )

    assert owned == [own_site, own_cdn]
    assert foreign == []


def test_scribd_keeps_its_own_asset_host() -> None:
    asset = candidate("https://html.scribdassets.com/abc/pages/1.jsonp")
    owned, foreign = DownloadEngine._split_third_party_candidates(
        [asset], ScribdProvider(), "https://www.scribd.com/document/12345"
    )

    assert owned == [asset]
    assert foreign == []


def test_generic_provider_keeps_every_candidate() -> None:
    # Without a known host there is no basis to call anything third-party.
    elsewhere = candidate("https://cdn.example.org/paper.pdf")
    owned, foreign = DownloadEngine._split_third_party_candidates(
        [elsewhere], GenericProvider(), "https://unknown-site.test/page"
    )

    assert owned == [elsewhere]
    assert foreign == []
