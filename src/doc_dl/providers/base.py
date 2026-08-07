from __future__ import annotations

from typing import Any

from doc_dl.models import ImagePageSet


class Provider:
    name = "generic"
    supports_authentication = False
    supports_render = True

    # Extra hosts, beyond the page's own site, that serve this provider's real
    # document files. Used to tell the page's own document apart from
    # third-party files it merely links to, such as works cited on a slide.
    document_host_suffixes: tuple[str, ...] = ()

    def match(self, url: str) -> int:
        return 0

    def normalize(self, url: str) -> str:
        return url

    def browser_url(self, url: str) -> str:
        return self.normalize(url)

    def login_url(self) -> str | None:
        return None

    def page_title(self, page: Any) -> str | None:
        """An accurate document title, if the provider can find one better than
        the browser page's own <title>. Return None to fall back to page.title()."""
        del page
        return None

    def activate(self, page: Any, timeout_ms: float) -> None:
        del page, timeout_ms

    def image_pages_from_html(self, html: str, url: str) -> ImagePageSet | None:
        """A title and ordered list of full-resolution page image URLs
        parsed directly from a fetched landing page, needing no browser at
        all. Return None if this provider has no such static reconstruction
        for the given page."""
        del html, url
        return None

    def render_page_numbers(self, page: Any) -> list[int]:
        del page
        return []

    def load_render_page(self, page: Any, page_number: int, timeout_ms: float) -> str | None:
        del page, page_number, timeout_ms
        return None

    def release_render_page(self, page: Any, page_number: int) -> None:
        del page, page_number
