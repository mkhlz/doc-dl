from __future__ import annotations

import logging
import re
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from doc_dl.config import StatePaths
from doc_dl.discovery import discover_document_candidates
from doc_dl.errors import DocDlError
from doc_dl.events import EventSink
from doc_dl.filenames import filename_from_content_disposition, sanitize_filename
from doc_dl.models import (
    BrowserDiscovery,
    CandidateKind,
    DocumentCandidate,
    DownloadRequest,
)
from doc_dl.providers.base import Provider
from doc_dl.render import PdfRenderer
from doc_dl.runtime import configure_browsers_path, install_chromium, is_chromium_installed
from doc_dl.verify import base_media_type, response_looks_document_like

_DOWNLOAD_TEXT = re.compile(r"\b(download|export|save\s+(?:as|file|pdf)|get\s+pdf)\b", re.I)

# Ad and analytics beacons routinely serve their diagnostic pings as
# text/plain at a URL ending in something like "f.txt" or "gen_204", which
# otherwise passes response_looks_document_like's plain-text-with-a-document-
# extension check. A news or blog page loads dozens of these, and without
# this filter each one gets reported as a candidate document.
_AD_TRACKER_HOSTS = (
    "doubleclick.net",
    "googlesyndication.com",
    "google-analytics.com",
    "googletagmanager.com",
    "googletagservices.com",
    "adnxs.com",
    "amazon-adsystem.com",
    "adsrvr.org",
    "criteo.com",
    "taboola.com",
    "outbrain.com",
    "scorecardresearch.com",
    "quantserve.com",
    "moatads.com",
    "casalemedia.com",
    "pubmatic.com",
    "rubiconproject.com",
    "openx.net",
    "bidswitch.net",
)


def _is_ad_tracker_host(hostname: str) -> bool:
    return any(hostname == host or hostname.endswith(f".{host}") for host in _AD_TRACKER_HOSTS)


# Playwright's sync API drives an asyncio connection on a background thread.
# When that thread's event loop tears down, pending Task/Future objects log a
# "Task was destroyed but it is pending!" warning through the stdlib asyncio
# logger. It is a known, harmless artifact of the sync-over-async bridge
# (reproduces even on a fully successful run) and confuses users into
# thinking the browser crashed, so it is silenced here.
logging.getLogger("asyncio").setLevel(logging.CRITICAL)


class BrowserExtractor:
    def __init__(self, sink: EventSink, state: StatePaths | None = None) -> None:
        self.sink = sink
        self.state = state or StatePaths.discover()

    def _ensure_chromium(self) -> None:
        configure_browsers_path(self.state)
        if is_chromium_installed():
            return
        install_chromium(self.sink, state=self.state)

    def discover(
        self,
        provider: Provider,
        url: str,
        request: DownloadRequest,
        *,
        force_render: bool = False,
    ) -> BrowserDiscovery:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise DocDlError(
                "browser_unavailable",
                "Playwright is not installed",
                detail=(
                    "Install doc-dl dependencies and run 'python -m playwright install chromium'."
                ),
            ) from exc

        self._ensure_chromium()
        discovery = BrowserDiscovery()
        profile_path = self.state.profile(provider.name, request.profile)
        try:
            profile_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DocDlError(
                "filesystem_failure",
                "The isolated browser profile could not be prepared",
                detail=str(exc),
            ) from exc

        timeout_ms = max(1_000.0, request.timeout_seconds * 1000)
        deadline = time.monotonic() + request.timeout_seconds
        temp_files: list[tuple[Path, str | None, str | None]] = []
        network_candidates: list[DocumentCandidate] = []
        download_events: list[Any] = []

        try:
            with sync_playwright() as playwright:
                try:
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir=str(profile_path),
                        channel="chromium",
                        headless=True,
                        accept_downloads=True,
                        viewport={"width": 1440, "height": 1000},
                        locale="en-US",
                    )
                except PlaywrightError as exc:
                    message = str(exc)
                    identifier = (
                        "browser_unavailable"
                        if "Executable doesn't exist" in message or "playwright install" in message
                        else "browser_failed"
                    )
                    raise DocDlError(
                        identifier,
                        "Chromium could not be started",
                        detail=message,
                    ) from exc

                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.set_default_timeout(min(timeout_ms, 30_000))
                    self._attach_observers(page, download_events, network_candidates, provider)
                    target_url = provider.browser_url(url)
                    self.sink.emit(
                        "strategy",
                        message=f"Opening browser page for provider {provider.name}",
                        strategy="browser",
                        status="started",
                        url=target_url,
                    )
                    try:
                        page.goto(
                            target_url,
                            wait_until="domcontentloaded",
                            timeout=min(timeout_ms, 60_000),
                        )
                    except PlaywrightTimeout as exc:
                        if time.monotonic() >= deadline:
                            raise DocDlError(
                                "operation_timeout",
                                "The browser navigation deadline expired",
                            ) from exc
                    except PlaywrightError as exc:
                        if "Download is starting" not in str(exc):
                            raise DocDlError(
                                "browser_failed",
                                "The browser could not open the document page",
                                detail=str(exc),
                            ) from exc

                    discovery.final_url = page.url
                    discovery.title = provider.page_title(page) or page.title()
                    try:
                        provider.activate(page, min(30_000, self._remaining_ms(deadline)))
                    except DocDlError as exc:
                        if exc.identifier == "authentication_required":
                            discovery.authentication_required = True
                        elif provider.name != "generic":
                            raise

                    if provider.name == "generic":
                        self._click_download_controls(page, download_events, deadline)

                    self._adaptive_scroll(page, deadline)
                    self._wait_for_page_images(page, deadline)

                    dynamic_html = page.content()
                    dynamic_candidates = discover_document_candidates(
                        dynamic_html,
                        page.url,
                        provider=provider.name,
                        strategy="browser-dom",
                    )
                    network_candidates.extend(dynamic_candidates)

                    cookies = self._cookie_dict(context.cookies())
                    user_agent = str(page.evaluate("() => navigator.userAgent"))
                    for candidate in network_candidates:
                        candidate.cookies.update(cookies)
                        candidate.headers.setdefault("Referer", page.url)
                        candidate.headers.setdefault("User-Agent", user_agent)

                    for download in download_events:
                        failure = download.failure()
                        if failure:
                            continue
                        suggested = sanitize_filename(download.suggested_filename or "document")
                        temporary = self._temporary_file(Path(suggested).suffix or ".download")
                        download.save_as(str(temporary))
                        temp_files.append((temporary, suggested, None))

                    discovery.downloaded_files = temp_files
                    discovery.candidates = self._dedupe(network_candidates)
                    self._detect_access_state(page, discovery, provider)

                    if (
                        not discovery.downloaded_files
                        and (force_render or not discovery.candidates)
                        and request.allow_render
                        and not request.original_only
                        and provider.supports_render
                        and not discovery.authentication_required
                        and not discovery.access_denied
                    ):
                        renderer = PdfRenderer(self.sink)
                        artifact = renderer.render(
                            page,
                            provider,
                            timeout_ms=min(30_000, self._remaining_ms(deadline)),
                        )
                        discovery.rendered_file = artifact.path
                        discovery.rendered_provenance = artifact.provenance
                        discovery.rendered_page_count = artifact.page_count
                        title = sanitize_filename(discovery.title or "document")
                        discovery.rendered_filename = f"{Path(title).stem}.pdf"
                finally:
                    context.close()
        except DocDlError:
            raise
        except Exception as exc:
            raise DocDlError(
                "browser_failed",
                "The browser extraction pipeline failed",
                detail=str(exc),
            ) from exc
        return discovery

    def login(self, provider: Provider, profile: str) -> None:
        login_url = provider.login_url()
        if not login_url or not provider.supports_authentication:
            raise DocDlError(
                "unsupported_url",
                f"Provider {provider.name} does not define an interactive login flow",
            )
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise DocDlError("browser_unavailable", "Playwright is not installed") from exc

        self._ensure_chromium()
        profile_path = self.state.profile(provider.name, profile)
        profile_path.mkdir(parents=True, exist_ok=True)
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_path),
                    channel="chromium",
                    headless=False,
                    accept_downloads=False,
                    viewport={"width": 1280, "height": 900},
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)
                    print(f"Complete the {provider.name} sign-in in the browser, then return here.")
                    input("Press Enter after sign-in is complete: ")
                finally:
                    context.close()
        except PlaywrightError as exc:
            raise DocDlError(
                "browser_failed",
                "The interactive login browser failed",
                detail=str(exc),
            ) from exc

    @staticmethod
    def _attach_observers(
        page: Any,
        downloads: list[Any],
        candidates: list[DocumentCandidate],
        provider: Provider,
    ) -> None:
        page.on("download", lambda download: downloads.append(download))

        def on_response(response: Any) -> None:
            try:
                hostname = (urlsplit(response.url).hostname or "").casefold()
                if _is_ad_tracker_host(hostname):
                    return
                headers = response.headers
                media_type = base_media_type(headers.get("content-type"))
                disposition = headers.get("content-disposition")
                content_length = BrowserExtractor._content_length(headers.get("content-length"))
                if content_length == 0:
                    return
                if not response_looks_document_like(
                    url=response.url,
                    media_type=media_type,
                    content_disposition=disposition,
                ):
                    return
                filename = filename_from_content_disposition(disposition)
                candidates.append(
                    DocumentCandidate(
                        url=response.url,
                        strategy="browser-network",
                        provider=provider.name,
                        kind=CandidateKind.NETWORK_RESPONSE,
                        media_type=media_type,
                        filename=filename,
                        size=content_length,
                        confidence=95,
                    )
                )
            except Exception:
                return

        page.on("response", on_response)

    @staticmethod
    def _content_length(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            length = int(value)
        except ValueError:
            return None
        return length if length >= 0 else None

    def _click_download_controls(
        self,
        page: Any,
        downloads: list[Any],
        deadline: float,
    ) -> None:
        selectors = (
            "a[download]",
            "a[href*='download' i]",
            "button:has-text('Download')",
            "a:has-text('Download')",
        )
        attempted = 0
        for selector in selectors:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 3)):
                if attempted >= 4 or time.monotonic() >= deadline:
                    return
                target = locator.nth(index)
                try:
                    if not target.is_visible():
                        continue
                    text = (target.inner_text(timeout=1_000) or "").strip()
                    if text and not _DOWNLOAD_TEXT.search(text) and selector != "a[download]":
                        continue
                    before = len(downloads)
                    target.click(timeout=min(5_000, self._remaining_ms(deadline)))
                    page.wait_for_timeout(750)
                    attempted += 1
                    if len(downloads) > before:
                        return
                except Exception:
                    continue

    @staticmethod
    def _adaptive_scroll(page: Any, deadline: float) -> None:
        stable = 0
        previous_height = 0
        previous_loaded = -1
        while stable < 3 and time.monotonic() < deadline:
            state = page.evaluate(
                """
                () => {
                  const root = document.scrollingElement || document.documentElement;
                  const lazy = Array.from(document.querySelectorAll(
                    '[loading="lazy"], [data-src], [data-lazy], [data-doc-page]'
                  ));
                  const loaded = lazy.filter((element) => {
                    if (element.matches('[data-doc-page]')) {
                      return element.getAttribute('data-state') === 'loaded';
                    }
                    if (element.tagName === 'IMG') {
                      return element.complete && element.naturalWidth > 0;
                    }
                    return !element.hasAttribute('data-src');
                  }).length;
                  const atBottom = root.scrollTop + window.innerHeight >= root.scrollHeight - 4;
                  if (!atBottom) window.scrollBy(0, Math.max(400, window.innerHeight * 0.8));
                  return { height: root.scrollHeight, loaded, atBottom };
                }
                """
            )
            current_height = int(state["height"])
            current_loaded = int(state["loaded"])
            if (
                state["atBottom"]
                and current_height == previous_height
                and current_loaded == previous_loaded
            ):
                stable += 1
            else:
                stable = 0
            previous_height = current_height
            previous_loaded = current_loaded
            page.wait_for_timeout(250)

    @staticmethod
    def _wait_for_page_images(page: Any, deadline: float) -> None:
        timeout_ms = min(10_000, max(0, round((deadline - time.monotonic()) * 1000)))
        if timeout_ms <= 0:
            return
        try:
            page.wait_for_function(
                """
                () => Array.from(document.querySelectorAll('[data-doc-page] img'))
                  .every((image) => image.complete && image.naturalWidth > 0)
                """,
                timeout=timeout_ms,
            )
        except Exception:
            return

    @staticmethod
    def _detect_access_state(page: Any, discovery: BrowserDiscovery, provider: Provider) -> None:
        current = page.url.casefold()
        content = page.content().casefold()
        has_password = page.locator("input[type='password']").count() > 0
        login_route = any(token in current for token in ("/login", "/signin", "/sign-in"))
        if provider.supports_authentication and has_password and login_route:
            discovery.authentication_required = True
        denied_phrases = (
            "access denied",
            "you do not have permission",
            "document is private",
            "content is unavailable",
        )
        if any(phrase in content for phrase in denied_phrases):
            discovery.access_denied = True

    @staticmethod
    def _cookie_dict(cookies: list[dict[str, Any]]) -> dict[str, str]:
        return {
            str(cookie["name"]): str(cookie["value"])
            for cookie in cookies
            if cookie.get("name") is not None and cookie.get("value") is not None
        }

    @staticmethod
    def _dedupe(candidates: list[DocumentCandidate]) -> list[DocumentCandidate]:
        best: dict[str, DocumentCandidate] = {}
        for candidate in candidates:
            current = best.get(candidate.url)
            if current is None or candidate.confidence > current.confidence:
                best[candidate.url] = candidate
        return sorted(best.values(), key=lambda item: item.confidence, reverse=True)

    @staticmethod
    def _remaining_ms(deadline: float) -> float:
        return max(1_000.0, (deadline - time.monotonic()) * 1000)

    @staticmethod
    def _temporary_file(suffix: str) -> Path:
        with tempfile.NamedTemporaryFile(
            prefix="doc-dl-browser-", suffix=suffix, delete=False
        ) as handle:
            path = Path(handle.name)
        return path
