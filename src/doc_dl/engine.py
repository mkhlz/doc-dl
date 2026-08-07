from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from doc_dl.discovery import discover_document_candidates
from doc_dl.errors import DocDlError
from doc_dl.events import EventSink
from doc_dl.filenames import apply_filename_template, resolve_output_path, sanitize_filename
from doc_dl.http import HttpDownloader, LandingPage, RetrievedDocument
from doc_dl.imageset import ImageSetReconstructor
from doc_dl.models import (
    CandidateKind,
    DocumentCandidate,
    DownloadRequest,
    DownloadResult,
    Provenance,
    StrategyRecord,
    StrategyStatus,
)
from doc_dl.providers.base import Provider
from doc_dl.providers.registry import ProviderRegistry
from doc_dl.redaction import redact_url
from doc_dl.urls import host_of, same_site, validate_url
from doc_dl.verify import ensure_document_extension, verify_document


class DownloadEngine:
    def __init__(
        self,
        sink: EventSink,
        *,
        registry: ProviderRegistry | None = None,
        http: HttpDownloader | None = None,
    ) -> None:
        self.sink = sink
        self.registry = registry or ProviderRegistry()
        self.http = http or HttpDownloader(sink)

    def download(self, request: DownloadRequest) -> DownloadResult:
        started = time.monotonic()
        self._access_error: DocDlError | None = None
        request.url = validate_url(request.url)
        provider = self.registry.select(request.url, request.forced_provider)
        source_url = provider.normalize(request.url)
        records: list[StrategyRecord] = []
        self.sink.emit(
            "start",
            message=f"Resolving {redact_url(request.url)}",
            source_url=request.url,
        )
        self.sink.emit(
            "provider_selected",
            message=f"Provider: {provider.name}",
            provider=provider.name,
        )

        landing_pages: list[LandingPage] = []
        direct = DocumentCandidate(
            url=source_url,
            strategy="direct-http",
            provider=provider.name,
            kind=CandidateKind.ORIGINAL,
            confidence=50,
        )
        direct_result = self._attempt_http(direct, request, records, landing_pages)
        if direct_result:
            return self._finish_http(
                direct_result,
                request,
                provider,
                records,
                started,
                Provenance.ORIGINAL,
            )

        # A provider that recognises this page knows what it actually hosts, so
        # give its own reconstruction precedence over links merely found on the
        # page. Otherwise a slide deck that cites a PDF would download the
        # citation instead of the deck.
        reconstruction_error: DocDlError | None = None
        if not request.original_only and request.allow_render:
            image_result, reconstruction_error = self._attempt_image_reconstruction(
                provider, landing_pages, request, records, started
            )
            if image_result:
                return image_result

        static_candidates: list[DocumentCandidate] = []
        for landing in landing_pages:
            static_candidates.extend(
                discover_document_candidates(
                    landing.html,
                    landing.url,
                    provider=provider.name,
                    strategy="static-html",
                )
            )
        static_candidates = self._dedupe_candidates(static_candidates)
        static_candidates, third_party = self._split_third_party_candidates(
            static_candidates, provider, source_url
        )
        if third_party:
            self._record(
                records,
                "static-html",
                StrategyStatus.SKIPPED,
                "third_party_reference",
                (
                    f"Ignored {len(third_party)} link(s) to other sites; on a "
                    f"{provider.name} page those are references, not the document"
                ),
                0,
            )
        self._record(
            records,
            "static-html",
            StrategyStatus.SUCCEEDED if static_candidates else StrategyStatus.SKIPPED,
            "candidates_found" if static_candidates else "candidate_not_found",
            f"Discovered {len(static_candidates)} static document candidate(s)",
            0,
        )
        for candidate in static_candidates:
            self.sink.emit(
                "candidate",
                message=f"Trying static candidate: {redact_url(candidate.url)}",
                url=candidate.url,
                confidence=candidate.confidence,
                strategy=candidate.strategy,
            )
            result = self._attempt_http(candidate, request, records, [])
            if result:
                return self._finish_http(
                    result,
                    request,
                    provider,
                    records,
                    started,
                    Provenance.ORIGINAL,
                )

        if not request.browser_enabled:
            if reconstruction_error:
                raise reconstruction_error
            self._raise_access_error(provider)
            raise DocDlError(
                "candidate_not_found",
                "No verified document was found without browser escalation",
            )

        from doc_dl.browser import BrowserExtractor

        browser_started = time.monotonic()
        browser = BrowserExtractor(self.sink)
        try:
            discovery = browser.discover(provider, source_url, request)
        except DocDlError as exc:
            self._record(
                records,
                "browser",
                StrategyStatus.FAILED,
                exc.identifier,
                exc.message,
                self._elapsed(browser_started),
            )
            if reconstruction_error:
                raise reconstruction_error from exc
            raise

        self._record(
            records,
            "browser",
            StrategyStatus.SUCCEEDED,
            "browser_inspected",
            (
                f"Captured {len(discovery.downloaded_files)} download(s) and "
                f"{len(discovery.candidates)} response candidate(s)"
            ),
            self._elapsed(browser_started),
        )

        browser_candidates, _ = self._split_third_party_candidates(
            self._dedupe_candidates(discovery.candidates), provider, source_url
        )
        for candidate in browser_candidates:
            result = self._attempt_http(candidate, request, records, [])
            if result:
                return self._finish_http(
                    result,
                    request,
                    provider,
                    records,
                    started,
                    Provenance.ORIGINAL,
                )

        for local_path, suggested_filename, media_type_hint in discovery.downloaded_files:
            try:
                result = self._commit_local_artifact(
                    local_path,
                    suggested_filename or "document",
                    media_type_hint,
                    request,
                    provider,
                    records,
                    started,
                    Provenance.ORIGINAL,
                )
                return result
            except DocDlError as exc:
                self._record(
                    records,
                    "browser-download",
                    StrategyStatus.FAILED,
                    exc.identifier,
                    exc.message,
                    0,
                )

        if (
            not discovery.rendered_file
            and (discovery.candidates or discovery.downloaded_files)
            and request.allow_render
            and not request.original_only
            and provider.supports_render
            and not discovery.authentication_required
            and not discovery.access_denied
        ):
            render_started = time.monotonic()
            try:
                fallback = browser.discover(
                    provider,
                    source_url,
                    request,
                    force_render=True,
                )
            except DocDlError as exc:
                self._record(
                    records,
                    "browser-render-fallback",
                    StrategyStatus.FAILED,
                    exc.identifier,
                    exc.message,
                    self._elapsed(render_started),
                )
                if reconstruction_error:
                    raise reconstruction_error from exc
                raise
            self._record(
                records,
                "browser-render-fallback",
                StrategyStatus.SUCCEEDED if fallback.rendered_file else StrategyStatus.SKIPPED,
                "rendered" if fallback.rendered_file else "render_unavailable",
                (
                    "Prepared a verified reconstruction fallback"
                    if fallback.rendered_file
                    else "The browser could not prepare a reconstruction fallback"
                ),
                self._elapsed(render_started),
            )
            if fallback.rendered_file:
                discovery.rendered_file = fallback.rendered_file
                discovery.rendered_filename = fallback.rendered_filename
                discovery.rendered_provenance = fallback.rendered_provenance
                discovery.rendered_page_count = fallback.rendered_page_count
            discovery.authentication_required = (
                discovery.authentication_required or fallback.authentication_required
            )
            discovery.access_denied = discovery.access_denied or fallback.access_denied

        if discovery.rendered_file and not request.original_only and request.allow_render:
            return self._commit_local_artifact(
                discovery.rendered_file,
                discovery.rendered_filename or "document.pdf",
                "application/pdf",
                request,
                provider,
                records,
                started,
                discovery.rendered_provenance or Provenance.RECONSTRUCTED,
                expected_pages=discovery.rendered_page_count,
            )

        if discovery.authentication_required:
            raise DocDlError(
                "authentication_required",
                (
                    f"Run 'doc-dl login {provider.name}' and retry the document"
                    if provider.supports_authentication
                    else "This document is only available to a signed-in session"
                ),
                detail=provider.access_hint(),
            )
        if discovery.access_denied:
            raise DocDlError(
                "access_denied",
                "The current profile cannot access this document",
                detail=provider.access_hint(),
            )
        if reconstruction_error:
            raise reconstruction_error
        self._raise_access_error(provider)
        raise DocDlError(
            "candidate_not_found",
            "No verified original or reconstructable document was found",
        )

    def _raise_access_error(self, provider: Provider) -> None:
        """Re-raise a sign-in or permission failure seen earlier, with whatever
        the provider can tell the person about getting access."""
        error = self._access_error
        if not error:
            return
        raise DocDlError(
            error.identifier,
            error.message,
            detail=provider.access_hint() or error.detail,
        )

    def _attempt_http(
        self,
        candidate: DocumentCandidate,
        request: DownloadRequest,
        records: list[StrategyRecord],
        landing_pages: list[LandingPage],
    ) -> RetrievedDocument | None:
        started = time.monotonic()
        try:
            result = self.http.fetch(candidate, request)
        except DocDlError as exc:
            if exc.identifier in {
                "authentication_required",
                "access_denied",
                "network_failure",
                "retry_exhausted",
                "unexpected_content",
                "corrupt_document",
                "verification_failed",
            }:
                if exc.identifier in {"authentication_required", "access_denied"}:
                    # Being told the file is private is far more useful than the
                    # generic "nothing found" a later stage would report, so keep
                    # it to raise once every other strategy has been tried.
                    self._access_error = self._access_error or exc
                self._record(
                    records,
                    candidate.strategy,
                    StrategyStatus.FAILED,
                    exc.identifier,
                    exc.message,
                    self._elapsed(started),
                )
                return None
            raise
        if isinstance(result, LandingPage):
            landing_pages.append(result)
            self._record(
                records,
                candidate.strategy,
                StrategyStatus.SKIPPED,
                "html_landing_page",
                "The response was an HTML landing page",
                self._elapsed(started),
            )
            return None
        self._record(
            records,
            candidate.strategy,
            StrategyStatus.SUCCEEDED,
            "verified_document",
            f"Downloaded and verified {result.filename}",
            self._elapsed(started),
        )
        return result

    @staticmethod
    def _split_third_party_candidates(
        candidates: list[DocumentCandidate],
        provider: Provider,
        source_url: str,
    ) -> tuple[list[DocumentCandidate], list[DocumentCandidate]]:
        """Separate documents plausibly belonging to this page from files it
        merely links to elsewhere.

        Only applied for providers that recognise the site: on a known
        document host, a link pointing off-site is a reference (a work cited on
        a slide, a related paper), not the document being requested. The
        generic provider has no such context, so its candidates are unchanged.
        """
        if provider.name == "generic":
            return candidates, []

        page_host = host_of(source_url)
        if not page_host:
            return candidates, []

        owned: list[DocumentCandidate] = []
        foreign: list[DocumentCandidate] = []
        for candidate in candidates:
            candidate_host = host_of(candidate.url)
            trusted = same_site(candidate_host, page_host) or any(
                candidate_host == suffix or candidate_host.endswith(f".{suffix}")
                for suffix in provider.document_host_suffixes
            )
            (owned if trusted else foreign).append(candidate)
        return owned, foreign

    def _attempt_image_reconstruction(
        self,
        provider: Provider,
        landing_pages: list[LandingPage],
        request: DownloadRequest,
        records: list[StrategyRecord],
        started: float,
    ) -> tuple[DownloadResult | None, DocDlError | None]:
        for landing in landing_pages:
            image_set = provider.image_pages_from_html(landing.html, landing.url)
            if not image_set or not image_set.image_urls:
                continue
            reconstruct_started = time.monotonic()
            try:
                local_path, page_count = ImageSetReconstructor(self.sink).reconstruct(
                    image_set, request.timeout_seconds
                )
            except DocDlError as exc:
                self._record(
                    records,
                    "image-reconstruction",
                    StrategyStatus.FAILED,
                    exc.identifier,
                    exc.message,
                    self._elapsed(reconstruct_started),
                )
                # Returned so the caller can surface this instead of whatever
                # the later fallbacks happen to say: this provider knew how to
                # rebuild the document, so its failure is the real cause.
                return None, exc
            self._record(
                records,
                "image-reconstruction",
                StrategyStatus.SUCCEEDED,
                "reconstructed",
                f"Reconstructed {page_count} page(s) from known page image URLs",
                self._elapsed(reconstruct_started),
            )
            committed = self._commit_local_artifact(
                local_path,
                image_set.title or "document",
                "application/pdf",
                request,
                provider,
                records,
                started,
                Provenance.RECONSTRUCTED,
                expected_pages=page_count,
            )
            return committed, None
        return None, None

    def _finish_http(
        self,
        retrieved: RetrievedDocument,
        request: DownloadRequest,
        provider: Provider,
        records: list[StrategyRecord],
        started: float,
        provenance: Provenance,
    ) -> DownloadResult:
        result = DownloadResult(
            path=retrieved.path,
            filename=retrieved.filename,
            media_type=retrieved.media_type,
            size=retrieved.size,
            provenance=provenance,
            provider=provider.name,
            source_url=request.url,
            elapsed_ms=self._elapsed(started),
            page_count=retrieved.page_count,
            strategies=records,
        )
        self._complete(result, request)
        return result

    def _commit_local_artifact(
        self,
        source: Path,
        suggested_filename: str,
        media_type_hint: str | None,
        request: DownloadRequest,
        provider: Provider,
        records: list[StrategyRecord],
        started: float,
        provenance: Provenance,
        expected_pages: int | None = None,
    ) -> DownloadResult:
        verification = verify_document(
            source,
            media_type_hint=media_type_hint,
            expected_pages=expected_pages,
        )
        default_name = sanitize_filename(suggested_filename)
        templated_name = apply_filename_template(
            request.filename_template,
            default_name,
            provider=provider.name,
        )
        filename = ensure_document_extension(
            templated_name,
            verification.media_type,
        )
        final_path = resolve_output_path(request.output, filename, overwrite=request.overwrite)
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
                "filesystem_failure",
                "The verified browser document could not be committed",
                detail=str(exc),
            ) from exc
        finally:
            source.unlink(missing_ok=True)

        verified_page_count: int | str = verification.page_count or "n/a"
        page_label = "page" if verification.page_count == 1 else "pages"
        self._record(
            records,
            "browser-artifact",
            StrategyStatus.SUCCEEDED,
            "verified_document",
            f"Verified {provenance.value} document with {verified_page_count} {page_label}",
            0,
        )
        result = DownloadResult(
            path=final_path,
            filename=final_path.name,
            media_type=verification.media_type,
            size=verification.size,
            provenance=provenance,
            provider=provider.name,
            source_url=request.url,
            elapsed_ms=self._elapsed(started),
            page_count=verification.page_count,
            strategies=records,
        )
        self._complete(result, request)
        return result

    def _complete(self, result: DownloadResult, request: DownloadRequest) -> None:
        if request.write_metadata:
            sidecar = result.path.with_name(f"{result.path.name}.doc-dl.json")
            payload = asdict(result)
            payload["path"] = str(result.path)
            payload["source_url"] = redact_url(result.source_url)
            temporary = sidecar.with_suffix(f"{sidecar.suffix}.tmp")
            try:
                temporary.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
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
            provider=result.provider,
            source_url=result.source_url,
            elapsed_ms=result.elapsed_ms,
            page_count=result.page_count,
        )

    def _record(
        self,
        records: list[StrategyRecord],
        strategy: str,
        status: StrategyStatus,
        reason_code: str,
        detail: str,
        elapsed_ms: int,
    ) -> None:
        record = StrategyRecord(strategy, status, reason_code, detail, elapsed_ms)
        records.append(record)
        self.sink.emit(
            "strategy",
            message=f"[{status.value}] {strategy}: {detail}",
            strategy=strategy,
            status=status.value,
            reason_code=reason_code,
            detail=detail,
            elapsed_ms=elapsed_ms,
        )

    @staticmethod
    def _elapsed(started: float) -> int:
        return round((time.monotonic() - started) * 1000)

    @staticmethod
    def _dedupe_candidates(candidates: list[DocumentCandidate]) -> list[DocumentCandidate]:
        best: dict[str, DocumentCandidate] = {}
        for candidate in candidates:
            current = best.get(candidate.url)
            if current is None or candidate.confidence > current.confidence:
                best[candidate.url] = candidate
        return sorted(best.values(), key=lambda item: item.confidence, reverse=True)
