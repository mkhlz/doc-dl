from __future__ import annotations

from typing import Any


class Provider:
    name = "generic"
    supports_authentication = False
    supports_render = True

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

    def render_page_numbers(self, page: Any) -> list[int]:
        del page
        return []

    def load_render_page(self, page: Any, page_number: int, timeout_ms: float) -> str | None:
        del page, page_number, timeout_ms
        return None

    def release_render_page(self, page: Any, page_number: int) -> None:
        del page, page_number
