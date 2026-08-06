from __future__ import annotations

import json
import re
from typing import Any

from doc_dl.models import ImagePageSet
from doc_dl.providers.base import Provider

_SLIDESHARE_URL = re.compile(r"^https?://(?:www\.)?slideshare\.net/", flags=re.IGNORECASE)
_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    flags=re.DOTALL | re.IGNORECASE,
)


def _dig(payload: Any, *keys: str) -> Any:
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


class SlideShareProvider(Provider):
    name = "slideshare"
    supports_authentication = False
    supports_render = True

    def match(self, url: str) -> int:
        return 100 if _SLIDESHARE_URL.match(url) else 0

    def image_pages_from_html(self, html: str, url: str) -> ImagePageSet | None:
        del url
        match = _NEXT_DATA.search(html)
        if not match:
            return None
        try:
            payload = json.loads(match.group(1))
        except (ValueError, TypeError):
            return None

        slideshow = _dig(payload, "props", "pageProps", "slideshow")
        if not isinstance(slideshow, dict):
            return None
        total = slideshow.get("totalSlides")
        slides = slideshow.get("slides")
        if not isinstance(total, int) or total <= 0 or not isinstance(slides, dict):
            return None

        host = slides.get("host")
        image_location = slides.get("imageLocation")
        title_slug = slides.get("title")
        sizes = slides.get("imageSizes")
        if not (
            isinstance(host, str)
            and isinstance(image_location, str)
            and isinstance(title_slug, str)
            and isinstance(sizes, list)
            and sizes
        ):
            return None

        best = max(
            (item for item in sizes if isinstance(item, dict) and "width" in item),
            key=lambda item: item["width"],
            default=None,
        )
        if best is None or "quality" not in best:
            return None

        # SlideShare serves these as image/webp regardless of the .jpg
        # extension in the URL; PIL detects the real format from content,
        # not the extension, so this is safe.
        urls = [
            f"{host}/{image_location}/{best['quality']}/{title_slug}-{page}-{best['width']}.jpg"
            for page in range(1, total + 1)
        ]
        title = slideshow.get("title")
        return ImagePageSet(title=title if isinstance(title, str) else None, image_urls=urls)
