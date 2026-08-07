from __future__ import annotations

import contextlib
import html
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from doc_dl.config import StatePaths
from doc_dl.errors import DocDlError
from doc_dl.events import EventSink
from doc_dl.filenames import apply_filename_template, resolve_output_path, sanitize_filename
from doc_dl.models import ArchiveRequest, ArchiveResult, Provenance
from doc_dl.render import temporary_pdf_path, write_image_page_pdf
from doc_dl.runtime import configure_browsers_path, install_chromium, is_chromium_installed
from doc_dl.urls import display_host, validate_url
from doc_dl.verify import verify_document

# See the note in browser.py: this is the same harmless sync-over-async
# teardown artifact, silenced for the same reason.
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

_MIN_ARTICLE_WORDS = 80

_PROVENANCE_PHRASES = {
    Provenance.PRINTED: "printed from the article text",
    Provenance.CAPTURED: "captured as a full-page image",
}

# A pragmatic, from-scratch readability heuristic (paragraph text density,
# scored up through two ancestor levels) rather than a vendored third-party
# library: it runs on the already-rendered DOM Playwright hands us, so it
# sees the same JS-built page a browser would, no separate fetch needed.
_EXTRACTION_JS = r"""
() => {
  const MIN_TEXT = 40;
  const NEGATIVE_WORDS = [
    "comment", "sidebar", "footer", "(^|[-_ ])nav([-_ ]|$)", "menu",
    "advert", "banner", "promo", "share", "subscribe", "related",
    "masthead", "toolbar", "cookie",
  ];
  const NEGATIVE = new RegExp(NEGATIVE_WORDS.join("|"), "i");
  const POSITIVE = /article|main|content|story|post|entry/i;

  const textOf = (el) => (el && el.innerText ? el.innerText.trim() : "");
  const classId = (el) => `${el.className || ""} ${el.id || ""}`;

  const scores = new Map();
  for (const p of document.querySelectorAll("p, pre")) {
    const text = textOf(p);
    if (text.length < MIN_TEXT) continue;
    let base = 1 + Math.min(3, Math.floor(text.length / 200));
    base += Math.min(2, (text.match(/[.!?]/g) || []).length / 4);
    let node = p.parentElement;
    let depth = 0;
    while (node && depth < 3) {
      const tag = classId(node);
      let weight = depth === 0 ? 1 : depth === 1 ? 0.6 : 0.3;
      if (NEGATIVE.test(tag)) weight *= 0.1;
      if (POSITIVE.test(tag)) weight *= 1.3;
      scores.set(node, (scores.get(node) || 0) + base * weight);
      node = node.parentElement;
      depth += 1;
    }
  }

  let best = null;
  let bestScore = 0;
  for (const [el, score] of scores) {
    if (score > bestScore) {
      best = el;
      bestScore = score;
    }
  }

  const article = document.querySelector("article");
  if (article && textOf(article).length > (best ? textOf(best).length * 0.6 : 200)) {
    best = article;
  }

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

  if (best) {
    for (const el of best.querySelectorAll("img, source")) {
      if (el.src) el.setAttribute("src", el.src);
      el.removeAttribute("srcset");
      el.removeAttribute("loading");
    }
    for (const el of best.querySelectorAll("a[href]")) {
      el.setAttribute("href", el.href);
    }
  }

  return {
    title,
    byline,
    published,
    siteName,
    canonicalUrl,
    paywallSuspected,
    articleHtml: best ? best.outerHTML : null,
    wordCount: best ? textOf(best).split(/\s+/).filter(Boolean).length : 0,
  };
}
"""

_PRINT_CSS = """
  body { font-family: Georgia, 'Times New Roman', serif; color: #1a1a1a;
         max-width: 720px; margin: 2.5cm auto; line-height: 1.5; font-size: 12pt; }
  h1 { font-size: 22pt; line-height: 1.2; margin-bottom: 0.3em; }
  .doc-dl-meta { color: #555; font-family: sans-serif; font-size: 9.5pt; margin-bottom: 1.5em; }
  img, video { max-width: 100%; height: auto; }
  a { color: #1a1a1a; text-decoration: underline; }
  figure { margin: 1em 0; }
  figcaption { font-size: 9.5pt; color: #555; }
"""


@dataclass(frozen=True, slots=True)
class ArticleExtraction:
    title: str | None
    byline: str | None
    published: str | None
    site_name: str | None
    canonical_url: str | None
    paywall_suspected: bool
    article_html: str | None
    word_count: int


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
        request.url = validate_url(request.url)
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
        profile_path = self.state.profile("archive", request.profile)
        try:
            profile_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DocDlError(
                "filesystem_failure",
                "The isolated browser profile could not be prepared",
                detail=str(exc),
            ) from exc

        self.sink.emit(
            "start",
            message=f"Resolving {request.url}",
            source_url=request.url,
            provider="archive",
            site=display_host(request.url),
        )

        timeout_ms = max(1_000.0, request.timeout_seconds * 1000)
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
                    return self._archive_in_context(context, request, started, timeout_ms)
                finally:
                    context.close()
        except DocDlError:
            raise
        except Exception as exc:
            raise DocDlError(
                "browser_failed", "The page archiving pipeline failed", detail=str(exc)
            ) from exc

    def _archive_in_context(
        self,
        context: Any,
        request: ArchiveRequest,
        started: float,
        timeout_ms: float,
    ) -> ArchiveResult:
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(min(timeout_ms, 30_000))
        try:
            page.goto(request.url, wait_until="domcontentloaded", timeout=min(timeout_ms, 60_000))
        except Exception as exc:
            raise DocDlError(
                "browser_failed", "The browser could not open the page", detail=str(exc)
            ) from exc

        final_url = page.url
        self._adaptive_scroll(page, time.monotonic() + request.timeout_seconds)

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

        wants_readability = request.mode in {"auto", "readability"}
        can_use_readability = (
            extraction.article_html is not None and extraction.word_count >= _MIN_ARTICLE_WORDS
        )
        output: Path | None = None
        provenance = Provenance.CAPTURED
        page_count = 1
        if wants_readability and can_use_readability:
            try:
                output = self._render_readability_pdf(context, extraction, final_url, timeout_ms)
                provenance = Provenance.PRINTED
            except DocDlError:
                if request.mode == "readability":
                    raise
                output = None
        if output is None:
            if request.mode == "readability":
                raise DocDlError(
                    "render_incomplete",
                    "Not enough readable article text was found on this page",
                )
            output = self._capture_screenshot_pdf(page, timeout_ms)
            provenance = Provenance.CAPTURED

        verification = verify_document(output, media_type_hint="application/pdf")
        page_count = verification.page_count or page_count
        return self._commit(
            output,
            verification,
            extraction,
            request,
            final_url,
            provenance,
            page_count,
            started,
        )

    def _render_readability_pdf(
        self,
        context: Any,
        extraction: ArticleExtraction,
        final_url: str,
        timeout_ms: float,
    ) -> Path:
        title = html.escape(extraction.title or "Untitled article")
        meta_bits = [
            html.escape(value)
            for value in (extraction.byline, extraction.published, extraction.site_name)
            if value
        ]
        meta_line = " &middot; ".join(meta_bits)
        document_html = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<base href='{html.escape(final_url, quote=True)}'>"
            f"<title>{title}</title><style>{_PRINT_CSS}</style></head><body>"
            f"<h1>{title}</h1>"
            f"<div class='doc-dl-meta'>{meta_line}</div>"
            f"{extraction.article_html}"
            "</body></html>"
        )
        print_page = context.new_page()
        output = temporary_pdf_path()
        try:
            print_page.set_content(document_html, timeout=min(timeout_ms, 30_000))
            with contextlib.suppress(Exception):
                print_page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8_000))
            print_page.emulate_media(media="print")
            print_page.pdf(
                path=str(output),
                format="A4",
                print_background=True,
                margin={"top": "1.5cm", "bottom": "1.5cm", "left": "1.5cm", "right": "1.5cm"},
            )
        except Exception as exc:
            output.unlink(missing_ok=True)
            raise DocDlError(
                "render_incomplete", "The article view could not be printed to PDF", detail=str(exc)
            ) from exc
        finally:
            print_page.close()
        return output

    def _capture_screenshot_pdf(self, page: Any, timeout_ms: float) -> Path:
        output = temporary_pdf_path()
        try:
            page.emulate_media(media="screen")
            png = page.screenshot(
                full_page=True,
                type="png",
                animations="disabled",
                caret="hide",
                timeout=min(timeout_ms, 30_000),
            )
        except Exception as exc:
            raise DocDlError(
                "browser_failed",
                "Chromium could not capture a full-page screenshot",
                detail=str(exc),
            ) from exc
        try:
            write_image_page_pdf(png, output)
        except DocDlError:
            output.unlink(missing_ok=True)
            raise
        return output

    @staticmethod
    def _extract(page: Any) -> ArticleExtraction:
        try:
            data = page.evaluate(_EXTRACTION_JS)
        except Exception:
            data = {}
        return ArticleExtraction(
            title=data.get("title"),
            byline=data.get("byline"),
            published=data.get("published"),
            site_name=data.get("siteName"),
            canonical_url=data.get("canonicalUrl"),
            paywall_suspected=bool(data.get("paywallSuspected")),
            article_html=data.get("articleHtml"),
            word_count=int(data.get("wordCount") or 0),
        )

    @staticmethod
    def _adaptive_scroll(page: Any, deadline: float) -> None:
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
        provenance: Provenance,
        page_count: int,
        started: float,
    ) -> ArchiveResult:
        default_name = sanitize_filename(extraction.title or "article") + ".pdf"
        templated_name = apply_filename_template(
            request.filename_template, default_name, provider="archive"
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
            provenance=provenance,
            source_url=request.url,
            final_url=final_url,
            title=extraction.title,
            byline=extraction.byline,
            published=extraction.published,
            site_name=extraction.site_name,
            canonical_url=extraction.canonical_url,
            paywall_suspected=extraction.paywall_suspected,
            elapsed_ms=round((time.monotonic() - started) * 1000),
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
                "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            temporary = sidecar.with_suffix(f"{sidecar.suffix}.tmp")
            try:
                import json

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
