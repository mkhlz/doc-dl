from __future__ import annotations

import re
from html import unescape
from typing import Any

from doc_dl.errors import DocDlError
from doc_dl.providers.base import Provider

_SCRIBD_DOCUMENT = re.compile(
    r"^https?://(?:www\.)?scribd\.com/(?:document|doc|presentation)/(\d+)(?:/[^?#]*)?",
    flags=re.IGNORECASE,
)
_SCRIBD_EMBED = re.compile(
    r"^https?://(?:www\.)?scribd\.com/embeds/(\d+)/content",
    flags=re.IGNORECASE,
)
_TITLE_TAG = re.compile(r"<title>([^<]*)</title>", re.IGNORECASE)
_TITLE_SUFFIX = re.compile(r"\s*\|\s*(?:PDF|Scribd|Document|Text|Text file)\b.*$", re.IGNORECASE)


def _clean_scribd_title(raw_title: str) -> str | None:
    """Strip Scribd's trailing ' | PDF | Scribd | Document' style suffix."""
    title = unescape(raw_title).strip()
    title = _TITLE_SUFFIX.sub("", title).strip()
    return title or None


class ScribdProvider(Provider):
    name = "scribd"
    supports_authentication = True
    supports_render = True
    document_host_suffixes = ("scribdassets.com",)

    def match(self, url: str) -> int:
        if _SCRIBD_DOCUMENT.match(url) or _SCRIBD_EMBED.match(url):
            return 100
        return 0

    def document_id(self, url: str) -> str:
        match = _SCRIBD_DOCUMENT.match(url) or _SCRIBD_EMBED.match(url)
        if not match:
            raise DocDlError("unsupported_url", "The URL is not a supported Scribd document URL")
        return match.group(1)

    def normalize(self, url: str) -> str:
        document_id = self.document_id(url)
        return f"https://www.scribd.com/document/{document_id}"

    def browser_url(self, url: str) -> str:
        return f"https://www.scribd.com/embeds/{self.document_id(url)}/content"

    def login_url(self) -> str:
        return "https://www.scribd.com/login"

    def page_title(self, page: Any) -> str | None:
        # The viewer is loaded from the embed URL, whose own <title> is just
        # "Scribd". The normal document page carries the real title instead.
        try:
            document_id = self.document_id(page.url)
        except DocDlError:
            return None
        try:
            response = page.request.get(
                f"https://www.scribd.com/document/{document_id}",
                timeout=10_000,
            )
            if not response.ok:
                return None
            html = response.text()
        except Exception:
            return None
        match = _TITLE_TAG.search(html)
        if not match:
            return None
        return _clean_scribd_title(match.group(1))

    def activate(self, page: Any, timeout_ms: float) -> None:
        try:
            page.wait_for_selector(".outer_page", state="attached", timeout=timeout_ms)
        except Exception as exc:
            current_url = str(getattr(page, "url", ""))
            if "login" in current_url.casefold() or "signin" in current_url.casefold():
                raise DocDlError(
                    "authentication_required",
                    "Scribd requires an authenticated profile for this document",
                ) from exc
            raise DocDlError(
                "extraction_failed",
                "The Scribd document viewer did not expose any pages",
                detail=str(exc),
            ) from exc

    def render_page_numbers(self, page: Any) -> list[int]:
        raw = page.evaluate(
            r"""
            () => {
              const manager = window.docManager;
              if (manager && manager.pages) {
                return Object.keys(manager.pages)
                  .map((value) => Number(value))
                  .filter((value) => Number.isInteger(value) && value > 0)
                  .sort((a, b) => a - b);
              }
              return Array.from(document.querySelectorAll('.outer_page'))
                .map((element, index) => {
                  const match = (element.id || '').match(/(\d+)$/);
                  return match ? Number(match[1]) : index + 1;
                });
            }
            """
        )
        return [int(item) for item in raw if int(item) > 0]

    def load_render_page(self, page: Any, page_number: int, timeout_ms: float) -> str | None:
        result = page.evaluate(
            """
            async ({ pageNumber, timeoutMs }) => {
              const manager = window.docManager;
              const state = manager && manager.pages ? manager.pages[pageNumber] : null;
              if (state) {
                try {
                  if (!state.innerPageElem && !state.loadHasStarted) state.load();
                } catch (error) {}
              }

              const started = Date.now();
              while (Date.now() - started < timeoutMs) {
                if (state && state.innerPageElem) {
                  try { state.display(); } catch (error) {}
                  try { state.turnOnImages(); } catch (error) {}
                }

                const byId = document.getElementById(`outer_page_${pageNumber}`);
                const pages = Array.from(document.querySelectorAll('.outer_page'));
                const target = byId || pages[pageNumber - 1] || null;
                if (target) {
                  const contentRoot = target.querySelector('.newpage') || target;
                  const images = Array.from(contentRoot.querySelectorAll('img'));
                  const imageSources = images.filter((image) => Boolean(
                    image.currentSrc || image.getAttribute('src') || image.dataset.src
                  ));
                  const imagesReady = imageSources.every(
                    (image) => image.complete && image.naturalWidth > 0
                  );
                  const hasLoadedImage = imageSources.some(
                    (image) => image.complete && image.naturalWidth > 0
                  );
                  const hasPopulatedSvg = Array.from(
                    contentRoot.querySelectorAll('svg')
                  ).some((svg) => svg.childElementCount > 0);
                  const hasCanvas = Array.from(
                    contentRoot.querySelectorAll('canvas')
                  ).some((canvas) => canvas.width > 1 && canvas.height > 1);
                  const textLength = Array.from(contentRoot.querySelectorAll('.text_layer'))
                    .reduce(
                      (total, layer) => total + (layer.textContent || '').trim().length,
                      0
                    );
                  const hasContent = (
                    hasLoadedImage || hasPopulatedSvg || hasCanvas || textLength >= 20
                  );
                  if (imagesReady && hasContent) {
                    // Scribd's scroll-virtualized viewer only flips a page's
                    // container out of `display: none` once its own internal
                    // scroll-position tracking considers it visible, which
                    // never happens for pages we jump to programmatically.
                    // The content above is already confirmed loaded, so force
                    // the container visible ourselves rather than waiting on
                    // that tracking to catch up.
                    target.style.setProperty('display', 'block', 'important');
                    if (!target.id) target.id = `doc_dl_outer_page_${pageNumber}`;
                    const outerSelector = `#${CSS.escape(target.id)}`;
                    const selector = contentRoot === target
                      ? outerSelector
                      : `${outerSelector} .newpage`;
                    return { ok: true, selector };
                  }
                }
                await new Promise((resolve) => setTimeout(resolve, 100));
              }
              return { ok: false, selector: null };
            }
            """,
            {"pageNumber": page_number, "timeoutMs": timeout_ms},
        )
        if not result or not result.get("ok"):
            raise DocDlError(
                "render_incomplete",
                f"Scribd page {page_number} did not finish loading",
            )
        return str(result["selector"])

    def release_render_page(self, page: Any, page_number: int) -> None:
        page.evaluate(
            """
            (pageNumber) => {
              const manager = window.docManager;
              const state = manager && manager.pages ? manager.pages[pageNumber] : null;
              if (!state) return;
              try { state.remove(); } catch (error) {}
            }
            """,
            page_number,
        )
