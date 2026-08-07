from __future__ import annotations

import contextlib
import io
import json
import logging
import math
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image
from pypdf import PdfWriter

from doc_dl.config import StatePaths
from doc_dl.errors import DocDlError
from doc_dl.events import EventSink
from doc_dl.filenames import apply_filename_template, resolve_output_path, sanitize_filename
from doc_dl.models import ArchiveRequest, ArchiveResult, Provenance
from doc_dl.render import (
    append_single_page,
    temporary_pdf_path,
    write_image_page_pdf,
    write_merged_pdf,
)
from doc_dl.runtime import configure_browsers_path, install_chromium, is_chromium_installed
from doc_dl.urls import display_host, validate_url
from doc_dl.verify import verify_document

# See the note in browser.py: this is the same harmless sync-over-async
# teardown artifact, silenced for the same reason.
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

_MAX_SCROLL_PAGES = 15
_MAX_EXPAND_ROUNDS = 5
_SCROLL_GROWTH_MULTIPLIER = 2
_SCROLL_GROWTH_FLOOR = 10_000

_PROVENANCE_PHRASES = {
    Provenance.CAPTURED: "captured as a full-page screenshot",
}

# Sites truncate the article body behind all sorts of differently-worded
# toggles that expand in place rather than navigating anywhere: mid-day.com's
# "Read More", indianexpress.com's bare "Expand", indiatoday.in's "Read Full
# Story", MSN's "Continue Reading", and theguardian.com's <label>-driven
# CSS-only show-more panels. A screenshot taken before these are clicked only
# captures the teaser, so every visible one is expanded first. Restricted to
# button-like elements (never <a>, so a genuine link to another article is
# never followed) and to elements outside nav/header/footer, so a site's own
# navigation dropdowns are left alone. "Expand" alone is matched exactly
# rather than as a substring, since it is common wording for unrelated
# things like an image lightbox trigger.
#
# aria-expanded="false" alone used to be treated as a signal too, on the
# theory that some custom toggle might not use obvious wording. In practice
# it matched far more than that: news.google.com alone has dozens of
# aria-expanded="false" elements that are the site's own menu, search box,
# and each story card's overflow ("...") button -- clicking those pops open
# a share/save menu that then sits in the screenshot, covering the actual
# content. Text matching is the only signal now.
_EXPAND_JS = r"""
() => {
  const TEXT_RE =
    /read\s*more|show\s*more|continue\s*reading|load\s*more|view\s*more/i.source +
    "|" + /read\s*full\s*story|full\s*story/i.source;
  const EXPAND_RE = new RegExp(TEXT_RE, "i");
  let clicked = 0;
  for (const el of document.querySelectorAll("button, [role='button'], summary, label")) {
    if (el.dataset.docDlExpandChecked) continue;
    el.dataset.docDlExpandChecked = "1";
    if (el.closest("nav, header, footer")) continue;
    const text = (el.innerText || el.getAttribute("aria-label") || "").trim();
    if (!EXPAND_RE.test(text) && !/^expand$/i.test(text)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    try {
      el.click();
      clicked += 1;
    } catch (e) {
      // Not clickable; leave it alone rather than risk a half-triggered handler.
    }
  }
  return clicked;
}
"""

# Consent/cookie banners are frequently rendered as a same- or cross-origin
# iframe (OneTrust, Sourcepoint/Fides, Didomi, Quantcast...), and often lock
# page scroll until dismissed -- which would otherwise make a screenshot
# capture show nothing but the banner (theguardian.com does exactly this).
# Playwright can interact with any frame regardless of origin, so every
# frame is checked, not just the main page. Exact-phrase matching turned out
# too brittle -- Sourcepoint's own accept button reads "Yes, I accept", which
# no reasonable phrase list would anticipate -- so this matches on keywords
# instead: containing an accept/agree word AND not a decline/manage-settings
# word, which covers far more vendors' actual wording without becoming a
# generic "click anything" tool.
_CONSENT_JS = r"""
() => {
  const POSITIVE = /accept|agree|allow all|got it|i understand/i;
  const NEGATIVE_WORDS = [
    "manage", "setting", "preference", "reject", "decline", "disagree",
    "necessary only", "essential only", "customi[sz]e", "more option",
  ];
  const NEGATIVE = new RegExp(NEGATIVE_WORDS.join("|"), "i");
  let clicked = 0;
  for (const el of document.querySelectorAll("button, [role='button'], a, input[type='button']")) {
    const text = (el.innerText || el.getAttribute("aria-label") || el.value || "").trim();
    if (text.length === 0 || text.length > 40) continue;
    if (!POSITIVE.test(text) || NEGATIVE.test(text)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    try {
      el.click();
      clicked += 1;
    } catch (e) {
      // Ignore; a stray unclickable match should never abort the capture.
    }
  }
  return clicked;
}
"""

# Only enough to label the capture and flag a likely paywall -- no attempt to
# extract, clean, or reconstruct the article body. The capture itself is a
# single full-page screenshot sliced into PDF pages, which is far more
# robust across arbitrary site layouts than trying to isolate and re-style
# "the article part" of an arbitrary page.
_METADATA_JS = r"""
() => {
  const textOf = (el) => (el && el.innerText ? el.innerText.trim() : "");
  const meta = (name) => {
    const el = document.querySelector(`meta[property="${name}"], meta[name="${name}"]`);
    return el ? el.getAttribute("content") : null;
  };

  const h1 = document.querySelector("h1");
  const title = meta("og:title") || textOf(h1) || document.title || null;
  const bylineEl = document.querySelector("[rel='author'], .byline, .author");
  const byline = meta("article:author") || meta("author") || textOf(bylineEl) || null;
  const timeEl = document.querySelector("time[datetime]");
  const timeAttr = timeEl && timeEl.getAttribute("datetime");
  const published = meta("article:published_time") || meta("date") || timeAttr || null;
  const siteName = meta("og:site_name") || location.hostname;
  const canonicalEl = document.querySelector("link[rel='canonical']");
  const canonicalUrl = (canonicalEl && canonicalEl.href) || meta("og:url") || location.href;

  let overlayCoverage = 0;
  const viewportArea = Math.max(1, window.innerWidth * window.innerHeight);
  for (const el of document.querySelectorAll("body *")) {
    const style = getComputedStyle(el);
    if (style.position !== "fixed" && style.position !== "sticky") continue;
    const rect = el.getBoundingClientRect();
    const area = Math.max(0, rect.width) * Math.max(0, rect.height);
    if (area / viewportArea > overlayCoverage) overlayCoverage = area / viewportArea;
  }
  const bodyOverflowHidden = getComputedStyle(document.body).overflow === "hidden";
  const bodyText = (document.body.innerText || "").toLowerCase();
  const paywallPhrases = [
    "subscribe to (continue|read)",
    "create a free account to continue",
    "you.?ve reached your (free )?article limit",
    "this content is (reserved|available) for subscribers",
    "log ?in to continue reading",
    "already a subscriber",
  ];
  const paywallWords = new RegExp(paywallPhrases.join("|"));
  const paywallSuspected =
    paywallWords.test(bodyText) || (overlayCoverage > 0.55 && bodyOverflowHidden);

  return { title, byline, published, siteName, canonicalUrl, paywallSuspected };
}
"""


@dataclass(frozen=True, slots=True)
class ArticleExtraction:
    title: str | None
    byline: str | None
    published: str | None
    site_name: str | None
    canonical_url: str | None
    paywall_suspected: bool


def _default_archive_filename(
    title: str | None, site_name: str | None, captured_at: datetime
) -> str:
    """Title alone is a poor filename for an archive: the same site's
    articles collide on generic titles ("Live Updates", "Google News"), and
    a folder of these sorts by name, not by when anything was actually
    captured. Site and date make that both distinguishable and sortable."""
    parts = [title or "article"]
    if site_name and site_name.casefold() != (title or "").casefold():
        parts.append(site_name)
    parts.append(captured_at.strftime("%Y-%m-%d"))
    return sanitize_filename(" - ".join(parts)) + ".pdf"


class PageArchiver:
    def __init__(self, sink: EventSink, state: StatePaths | None = None) -> None:
        self.sink = sink
        self.state = state or StatePaths.discover()

    def _ensure_chromium(self) -> None:
        configure_browsers_path(self.state)
        if is_chromium_installed():
            return
        install_chromium(self.sink, state=self.state)

    def archive(self, request: ArchiveRequest) -> ArchiveResult:
        started = time.monotonic()
        self.sink.emit(
            "start",
            message=f"Resolving {request.url}",
            source_url=request.url,
            provider="archive",
            site=display_host(request.url),
        )
        output, extraction, final_url = self.capture(
            request.url,
            profile=request.profile,
            timeout_seconds=request.timeout_seconds,
        )
        verification = verify_document(output, media_type_hint="application/pdf")
        captured_at = datetime.now(UTC)
        return self._commit(
            output,
            verification,
            extraction,
            request,
            final_url,
            verification.page_count or 1,
            started,
            captured_at,
        )

    def capture(
        self,
        url: str,
        *,
        profile: str = "default",
        timeout_seconds: float = 90.0,
    ) -> tuple[Path, ArticleExtraction, str]:
        """Snapshot a page as a PDF, one screenshot per scrolled viewport --
        the same page-by-page capture used for slide-deck reconstruction,
        just scrolling a normal page instead of stepping through a viewer.

        Returns the temporary PDF path, what little metadata could be read
        off the page, and the final URL, leaving verification and committing
        the file to its final location up to the caller -- `archive()` for
        the standalone command, or `DownloadEngine` when it falls back to
        this as a last resort for a page with no downloadable document.
        """
        url = validate_url(url)
        try:
            from playwright.sync_api import Error as PlaywrightError
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
        profile_path = self.state.profile("archive", profile)
        try:
            profile_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DocDlError(
                "filesystem_failure",
                "The isolated browser profile could not be prepared",
                detail=str(exc),
            ) from exc

        timeout_ms = max(1_000.0, timeout_seconds * 1000)
        try:
            with sync_playwright() as playwright:
                try:
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir=str(profile_path),
                        channel="chromium",
                        headless=True,
                        viewport={"width": 1280, "height": 1400},
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
                        identifier, "Chromium could not be started", detail=message
                    ) from exc
                try:
                    return self._capture_in_context(context, url, timeout_seconds, timeout_ms)
                finally:
                    context.close()
        except DocDlError:
            raise
        except Exception as exc:
            raise DocDlError(
                "browser_failed", "The page archiving pipeline failed", detail=str(exc)
            ) from exc

    def _capture_in_context(
        self,
        context: Any,
        url: str,
        timeout_seconds: float,
        timeout_ms: float,
    ) -> tuple[Path, ArticleExtraction, str]:
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(min(timeout_ms, 30_000))
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=min(timeout_ms, 60_000))
        except Exception as exc:
            raise DocDlError(
                "browser_failed", "The browser could not open the page", detail=str(exc)
            ) from exc

        final_url = page.url
        deadline = time.monotonic() + timeout_seconds
        self._dismiss_consent_banners(page, deadline)
        # Some sites (news.nytimes.com's metered wall is one) clip the
        # article's real height down to a single viewport once a login or
        # paywall prompt kicks in a few seconds after load. Reading the
        # height now, before that has a chance to happen, gives a floor that
        # a later, artificially shrunk reading can never undercut.
        early_height = self._safe_height(page)
        self._settle(page, timeout_seconds)
        self._dismiss_consent_banners(page, deadline)
        # Many news sites auto-load an endless "more from this site" feed
        # below the article itself; scrolling to the bottom to trigger lazy
        # images would otherwise chase that feed indefinitely. Growth is
        # allowed well past the article's own height (long articles are
        # real), but capped rather than unbounded.
        growth_ceiling = max(_SCROLL_GROWTH_FLOOR, early_height * _SCROLL_GROWTH_MULTIPLIER)
        self._expand_collapsed_content(page, deadline)
        self._adaptive_scroll(page, deadline, growth_ceiling)
        self._expand_collapsed_content(page, deadline)
        self._adaptive_scroll(page, deadline, growth_ceiling)
        # Widgets that fetch their own content on scroll (a "most viewed"
        # rail is a common one) can still be mid-request even once scrolling
        # itself has settled; give images a moment to actually finish.
        self._wait_for_images(page, min(5_000.0, self._remaining_ms(deadline)))

        extraction = self._extract(page)
        self.sink.emit(
            "document_info",
            site=display_host(final_url),
            title=extraction.title,
            facts=[
                fact
                for fact in (
                    extraction.byline,
                    extraction.published,
                    "paywall suspected" if extraction.paywall_suspected else None,
                )
                if fact
            ],
        )
        if extraction.paywall_suspected:
            self.sink.emit(
                "warning",
                message="This page shows signs of a paywall; the capture may be incomplete.",
            )

        output = self._capture_scroll_pages(
            page, timeout_ms, min_height=early_height, max_height=growth_ceiling
        )
        return output, extraction, final_url

    def _capture_scroll_pages(
        self,
        page: Any,
        timeout_ms: float,
        *,
        min_height: int = 0,
        max_height: int = _SCROLL_GROWTH_FLOOR,
    ) -> Path:
        """One continuous full-page screenshot, sliced into page-sized
        chunks -- the same idea as a scrolling-capture tool, just done with
        Chromium's own full-page screenshot instead of stepping the
        viewport manually. Scrolling to each position and screenshotting
        separately (the previous approach) could drift: an image lazy-
        loading between two of those shots, or a reflow from something
        settling, could shift where the "real" bottom of the page was by
        the time the last shot was taken, cropping or duplicating content.
        A single screenshot has no such gap -- there is exactly one moment
        being captured, and slicing it in Python afterwards cannot lose or
        repeat anything the browser did not already lose."""
        output = temporary_pdf_path()
        try:
            page.emulate_media(media="screen")
            with contextlib.suppress(Exception):
                page.evaluate("() => window.scrollTo(0, 0)")
            full_png = page.screenshot(
                full_page=True,
                type="png",
                animations="disabled",
                caret="hide",
                timeout=min(timeout_ms, 30_000),
            )
            image = Image.open(io.BytesIO(full_png))
            image.load()
            width, captured_height = image.size

            viewport = page.viewport_size or {"width": 1280, "height": 1400}
            slice_height = max(200, int(viewport["height"]))
            # The screenshot is ground truth for what the browser actually
            # rendered; the floor/ceiling here only bounds how many slices
            # of it are kept, guarding against a page that grew far past
            # what a single article needs (see the adaptive-scroll ceiling).
            effective_height = min(max_height, max(min_height, captured_height))
            page_count = min(_MAX_SCROLL_PAGES, max(1, math.ceil(effective_height / slice_height)))

            writer = PdfWriter()
            with tempfile.TemporaryDirectory(prefix="doc-dl-archive-spool-") as spool:
                spool_path = Path(spool)
                for index in range(page_count):
                    top = index * slice_height
                    if top >= captured_height:
                        break
                    bottom = min(captured_height, top + slice_height)
                    chunk = image.crop((0, top, width, bottom)).convert("RGB")
                    buffer = io.BytesIO()
                    chunk.save(buffer, format="PNG")
                    page_file = spool_path / f"page-{index + 1:06d}.pdf"
                    write_image_page_pdf(buffer.getvalue(), page_file)
                    append_single_page(writer, page_file, index + 1)
                    self.sink.emit(
                        "download_progress",
                        message=f"Assembling page {index + 1}/{page_count}",
                        downloaded=index + 1,
                        total=page_count,
                        unit="pages",
                    )
                write_merged_pdf(writer, output)
        except DocDlError:
            output.unlink(missing_ok=True)
            raise
        except Exception as exc:
            output.unlink(missing_ok=True)
            raise DocDlError(
                "browser_failed", "Chromium could not capture the page", detail=str(exc)
            ) from exc
        return output

    @staticmethod
    def _extract(page: Any) -> ArticleExtraction:
        try:
            data = page.evaluate(_METADATA_JS)
        except Exception:
            data = {}
        return ArticleExtraction(
            title=data.get("title"),
            byline=data.get("byline"),
            published=data.get("published"),
            site_name=data.get("siteName"),
            canonical_url=data.get("canonicalUrl"),
            paywall_suspected=bool(data.get("paywallSuspected")),
        )

    @staticmethod
    def _safe_height(page: Any) -> int:
        try:
            return int(page.evaluate("() => document.documentElement.scrollHeight"))
        except Exception:
            return 0

    @staticmethod
    def _settle(page: Any, timeout_seconds: float) -> None:
        """Give ads, lazy images, and consent widgets a real chance to load.

        Most ad-heavy news pages never truly go idle (tracking beacons keep
        firing), so this is capped rather than awaited indefinitely -- it is
        a "wait a reasonable while" step, not a correctness guarantee."""
        settle_budget = min(8_000, max(3_000, timeout_seconds * 150))
        with contextlib.suppress(Exception):
            page.wait_for_load_state("networkidle", timeout=settle_budget)
        page.wait_for_timeout(1_500)

    @staticmethod
    def _remaining_ms(deadline: float) -> float:
        return max(1_000.0, (deadline - time.monotonic()) * 1000)

    @staticmethod
    def _wait_for_images(page: Any, timeout_ms: float) -> None:
        with contextlib.suppress(Exception):
            page.wait_for_function(
                "() => Array.from(document.querySelectorAll('img')).every(img => img.complete)",
                timeout=timeout_ms,
            )

    @staticmethod
    def _dismiss_consent_banners(page: Any, deadline: float) -> None:
        for _ in range(3):
            if time.monotonic() >= deadline:
                return
            clicked = 0
            for frame in list(page.frames):
                try:
                    clicked += frame.evaluate(_CONSENT_JS) or 0
                except Exception:
                    continue
            if not clicked:
                return
            page.wait_for_timeout(400)

    @staticmethod
    def _expand_collapsed_content(page: Any, deadline: float) -> None:
        for _ in range(_MAX_EXPAND_ROUNDS):
            if time.monotonic() >= deadline:
                return
            try:
                clicked = page.evaluate(_EXPAND_JS)
            except Exception:
                return
            if not clicked:
                return
            page.wait_for_timeout(300)

    @staticmethod
    def _adaptive_scroll(page: Any, deadline: float, growth_ceiling: int) -> None:
        stable = 0
        previous_height = 0
        while stable < 3 and time.monotonic() < deadline:
            try:
                state = page.evaluate(
                    """
                    () => {
                      const root = document.scrollingElement || document.documentElement;
                      const atBottom = root.scrollTop + window.innerHeight >= root.scrollHeight - 4;
                      if (!atBottom) window.scrollBy(0, Math.max(400, window.innerHeight * 0.8));
                      return { height: root.scrollHeight, atBottom };
                    }
                    """
                )
            except Exception:
                return
            current_height = int(state["height"])
            if current_height >= growth_ceiling:
                break
            if state["atBottom"] and current_height == previous_height:
                stable += 1
            else:
                stable = 0
            previous_height = current_height
            page.wait_for_timeout(200)
        with contextlib.suppress(Exception):
            page.evaluate("() => window.scrollTo(0, 0)")

    def _commit(
        self,
        source: Path,
        verification: Any,
        extraction: ArticleExtraction,
        request: ArchiveRequest,
        final_url: str,
        page_count: int,
        started: float,
        captured_at: datetime,
    ) -> ArchiveResult:
        if request.filename_template:
            # A custom template's {title} should stay the plain article
            # title, not the verbose default with the site and date baked in.
            plain_default = sanitize_filename(extraction.title or "article") + ".pdf"
            templated_name = apply_filename_template(
                request.filename_template, plain_default, provider="archive"
            )
        else:
            templated_name = _default_archive_filename(
                extraction.title, extraction.site_name, captured_at
            )
        final_path = resolve_output_path(
            request.output, templated_name, overwrite=request.overwrite
        )
        staging = final_path.parent / f".{final_path.name}.doc-dl-staging"
        try:
            with source.open("rb") as input_handle, staging.open("wb") as output_handle:
                shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
                output_handle.flush()
                os.fsync(output_handle.fileno())
            os.replace(staging, final_path)
        except OSError as exc:
            staging.unlink(missing_ok=True)
            raise DocDlError(
                "filesystem_failure", "The archived page could not be committed", detail=str(exc)
            ) from exc
        finally:
            source.unlink(missing_ok=True)

        result = ArchiveResult(
            path=final_path,
            filename=final_path.name,
            media_type=verification.media_type,
            size=verification.size,
            provenance=Provenance.CAPTURED,
            source_url=request.url,
            final_url=final_url,
            title=extraction.title,
            byline=extraction.byline,
            published=extraction.published,
            site_name=extraction.site_name,
            canonical_url=extraction.canonical_url,
            paywall_suspected=extraction.paywall_suspected,
            elapsed_ms=round((time.monotonic() - started) * 1000),
            captured_at=captured_at.isoformat(timespec="seconds"),
            page_count=page_count,
        )
        self._complete(result, request)
        return result

    def _complete(self, result: ArchiveResult, request: ArchiveRequest) -> None:
        if request.write_metadata:
            sidecar = result.path.with_name(f"{result.path.name}.doc-dl.json")
            payload = {
                "event": "archive",
                "version": 1,
                "path": str(result.path),
                "filename": result.filename,
                "media_type": result.media_type,
                "size": result.size,
                "provenance": result.provenance.value,
                "source_url": result.source_url,
                "final_url": result.final_url,
                "title": result.title,
                "byline": result.byline,
                "published": result.published,
                "site_name": result.site_name,
                "canonical_url": result.canonical_url,
                "paywall_suspected": result.paywall_suspected,
                "page_count": result.page_count,
                "captured_at": result.captured_at,
            }
            temporary = sidecar.with_suffix(f"{sidecar.suffix}.tmp")
            try:
                temporary.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                os.replace(temporary, sidecar)
            except OSError as exc:
                temporary.unlink(missing_ok=True)
                raise DocDlError(
                    "filesystem_failure",
                    "The metadata sidecar could not be written",
                    detail=str(exc),
                ) from exc

        self.sink.emit(
            "complete",
            path=str(result.path),
            filename=result.filename,
            media_type=result.media_type,
            bytes=result.size,
            provenance=result.provenance.value,
            provider="archive",
            source_url=result.source_url,
            elapsed_ms=result.elapsed_ms,
            page_count=result.page_count,
            facts=self._result_facts(result),
        )

    @staticmethod
    def _result_facts(result: ArchiveResult) -> list[str]:
        from doc_dl.ui import format_bytes, format_duration

        facts = [format_bytes(result.size)]
        if result.page_count:
            facts.append(f"{result.page_count} pages")
        facts.append(_PROVENANCE_PHRASES[result.provenance])
        if result.paywall_suspected:
            facts.append("paywall suspected")
        facts.append(format_duration(result.elapsed_ms / 1000))
        return facts
