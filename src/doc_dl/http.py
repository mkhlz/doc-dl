from __future__ import annotations

import email.utils
import hashlib
import json
import os
import random
import re
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from doc_dl.errors import DocDlError
from doc_dl.events import EventSink
from doc_dl.filenames import (
    apply_filename_template,
    filename_from_content_disposition,
    filename_from_url,
    resolve_output_path,
    sanitize_filename,
)
from doc_dl.models import DocumentCandidate, DownloadRequest
from doc_dl.redaction import redact_url
from doc_dl.urls import normalized_url
from doc_dl.verify import (
    VerificationResult,
    base_media_type,
    ensure_document_extension,
    looks_like_html,
    response_looks_document_like,
    verify_document,
)

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
HTML_LIMIT = 8 * 1024 * 1024
CHUNK_SIZE = 128 * 1024
PROGRESS_INTERVAL_BYTES = 2 * 1024 * 1024


@dataclass(slots=True)
class LandingPage:
    url: str
    html: str
    media_type: str | None
    headers: dict[str, str]


@dataclass(slots=True)
class RetrievedDocument:
    path: Path
    media_type: str
    size: int
    page_count: int | None
    filename: str
    effective_url: str
    resumed: bool


@dataclass(slots=True)
class ResumeState:
    version: int
    source_url: str
    etag: str | None
    last_modified: str | None
    expected_total: int | None
    media_type: str | None
    filename: str | None


class HttpDownloader:
    def __init__(
        self,
        sink: EventSink,
        *,
        user_agent: str = "doc-dl/0.1",
        retry_base_delay: float = 0.5,
    ) -> None:
        self.sink = sink
        self.user_agent = user_agent
        self.retry_base_delay = retry_base_delay

    def fetch(
        self,
        candidate: DocumentCandidate,
        request: DownloadRequest,
    ) -> LandingPage | RetrievedDocument:
        deadline = time.monotonic() + request.timeout_seconds
        part_path, state_path = self._partial_paths(candidate.url, request.output)
        state = self._load_state(state_path)
        existing_size = part_path.stat().st_size if part_path.exists() else 0
        can_resume = bool(
            request.resume
            and state
            and state.source_url == normalized_url(candidate.url)
            and existing_size > 0
            and (state.etag or state.last_modified)
        )

        last_error: Exception | None = None
        for attempt in range(request.retries + 1):
            if time.monotonic() >= deadline:
                raise DocDlError("operation_timeout", "The download deadline expired")

            headers = {"User-Agent": self.user_agent, "Accept": "*/*", **candidate.headers}
            existing_size = part_path.stat().st_size if part_path.exists() else 0
            if can_resume and existing_size:
                headers["Range"] = f"bytes={existing_size}-"
                headers["If-Range"] = state.etag or state.last_modified or ""

            timeout_remaining = max(1.0, deadline - time.monotonic())
            timeout = httpx.Timeout(
                connect=min(20.0, timeout_remaining),
                read=min(60.0, timeout_remaining),
                write=min(60.0, timeout_remaining),
                pool=min(20.0, timeout_remaining),
            )
            try:
                with (
                    httpx.Client(
                        follow_redirects=True,
                        timeout=timeout,
                        cookies=candidate.cookies,
                    ) as client,
                    client.stream("GET", candidate.url, headers=headers) as response,
                ):
                    if response.status_code in RETRYABLE_STATUS:
                        delay = self._retry_delay(response, attempt, deadline)
                        self.sink.emit(
                            "retry",
                            message=(
                                f"HTTP {response.status_code}; retrying in {delay:.2f} seconds"
                            ),
                            attempt=attempt + 1,
                            status=response.status_code,
                            delay_seconds=delay,
                            url=str(response.url),
                        )
                        if attempt >= request.retries:
                            raise DocDlError(
                                "retry_exhausted",
                                f"HTTP {response.status_code} persisted after retries",
                            )
                        time.sleep(delay)
                        continue
                    if response.status_code == 401:
                        raise DocDlError(
                            "authentication_required",
                            "The document requires an authenticated session",
                        )
                    if response.status_code == 403:
                        raise DocDlError(
                            "access_denied",
                            "The server denied access to the document",
                        )
                    if response.status_code >= 400 and response.status_code != 416:
                        raise DocDlError(
                            "network_failure",
                            f"The server returned HTTP {response.status_code}",
                            detail=redact_url(str(response.url)),
                        )

                    result = self._consume_response(
                        response=response,
                        candidate=candidate,
                        request=request,
                        part_path=part_path,
                        state_path=state_path,
                        previous_state=state,
                        existing_size=existing_size,
                        range_requested="Range" in headers,
                    )
                    return result
            except DocDlError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                last_error = exc
                state = self._load_state(state_path)
                can_resume = bool(
                    request.resume
                    and state
                    and part_path.exists()
                    and part_path.stat().st_size > 0
                    and (state.etag or state.last_modified)
                )
                if attempt >= request.retries:
                    break
                delay = min(self.retry_base_delay * (2**attempt) + random.uniform(0, 0.25), 10.0)
                if time.monotonic() + delay >= deadline:
                    break
                self.sink.emit(
                    "retry",
                    message=f"Transfer interrupted; retrying in {delay:.2f} seconds",
                    attempt=attempt + 1,
                    delay_seconds=delay,
                    resumed=can_resume,
                    url=candidate.url,
                )
                time.sleep(delay)

        identifier = "retry_exhausted" if request.retries else "network_failure"
        raise DocDlError(
            identifier,
            "The document transfer could not be completed",
            detail=str(last_error) if last_error else None,
            retryable=True,
        )

    def _consume_response(
        self,
        *,
        response: httpx.Response,
        candidate: DocumentCandidate,
        request: DownloadRequest,
        part_path: Path,
        state_path: Path,
        previous_state: ResumeState | None,
        existing_size: int,
        range_requested: bool,
    ) -> LandingPage | RetrievedDocument:
        if response.status_code == 416 and previous_state and part_path.exists():
            verification = verify_document(part_path, media_type_hint=previous_state.media_type)
            return self._commit_part(
                part_path=part_path,
                state_path=state_path,
                request=request,
                candidate=candidate,
                response=response,
                verification=verification,
                filename=previous_state.filename,
                resumed=True,
            )

        media_type = base_media_type(response.headers.get("content-type"))
        content_disposition = response.headers.get("content-disposition")
        document_hint = (
            response_looks_document_like(
                url=str(response.url),
                media_type=media_type,
                content_disposition=content_disposition,
            )
            or candidate.confidence >= 80
        )

        if media_type in {"text/html", "application/xhtml+xml"} and not document_hint:
            data = self._read_limited(response, HTML_LIMIT)
            return LandingPage(
                url=str(response.url),
                html=self._decode_html(data, response),
                media_type=media_type,
                headers=dict(response.headers),
            )

        append = False
        resumed = False
        if range_requested and response.status_code == 206:
            range_start, range_total = self._parse_content_range(
                response.headers.get("content-range")
            )
            validators_match = self._validators_match(previous_state, response)
            if range_start == existing_size and validators_match:
                append = True
                resumed = True
                expected_total = range_total
            else:
                expected_total = range_total
        else:
            expected_total = self._content_total(response, 0)

        if not append:
            existing_size = 0

        server_filename = filename_from_content_disposition(content_disposition)
        filename = sanitize_filename(
            server_filename or candidate.filename or filename_from_url(str(response.url))
        )
        state = ResumeState(
            version=1,
            source_url=normalized_url(candidate.url),
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            expected_total=expected_total,
            media_type=media_type,
            filename=filename,
        )
        self._write_state(state_path, state)

        mode = "ab" if append else "wb"
        downloaded = existing_size
        next_progress = downloaded + PROGRESS_INTERVAL_BYTES
        try:
            with part_path.open(mode) as handle:
                first_chunk = True
                stream = response.iter_bytes(CHUNK_SIZE)
                for chunk in stream:
                    if not chunk:
                        continue
                    if first_chunk and not append and looks_like_html(chunk):
                        html_chunks = [chunk[:HTML_LIMIT]]
                        html_size = len(html_chunks[0])
                        for remainder in stream:
                            if not remainder or html_size >= HTML_LIMIT:
                                continue
                            remaining = HTML_LIMIT - html_size
                            html_chunks.append(remainder[:remaining])
                            html_size += min(len(remainder), remaining)
                        data = b"".join(html_chunks)
                        handle.close()
                        part_path.unlink(missing_ok=True)
                        state_path.unlink(missing_ok=True)
                        if document_hint:
                            raise DocDlError(
                                "unexpected_content",
                                "The server returned HTML where a document was expected",
                            )
                        return LandingPage(
                            url=str(response.url),
                            html=self._decode_html(data, response),
                            media_type=media_type,
                            headers=dict(response.headers),
                        )
                    first_chunk = False
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if downloaded >= next_progress:
                        self.sink.emit(
                            "download_progress",
                            downloaded=downloaded,
                            total=expected_total,
                            url=str(response.url),
                        )
                        next_progress = downloaded + PROGRESS_INTERVAL_BYTES
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise DocDlError(
                "filesystem_failure",
                "The partial document could not be written",
                detail=str(exc),
            ) from exc

        if expected_total is not None and downloaded != expected_total:
            raise httpx.RemoteProtocolError(
                f"Transfer ended at {downloaded} bytes; expected {expected_total}"
            )

        verification = verify_document(part_path, media_type_hint=media_type)
        return self._commit_part(
            part_path=part_path,
            state_path=state_path,
            request=request,
            candidate=candidate,
            response=response,
            verification=verification,
            filename=filename,
            resumed=resumed,
        )

    def _commit_part(
        self,
        *,
        part_path: Path,
        state_path: Path,
        request: DownloadRequest,
        candidate: DocumentCandidate,
        response: httpx.Response,
        verification: VerificationResult,
        filename: str | None,
        resumed: bool,
    ) -> RetrievedDocument:
        default_name = sanitize_filename(filename or candidate.filename or "document")
        templated_name = apply_filename_template(
            request.filename_template,
            default_name,
            provider=candidate.provider,
        )
        final_name = ensure_document_extension(
            templated_name,
            verification.media_type,
        )
        final_path = resolve_output_path(
            request.output,
            final_name,
            overwrite=request.overwrite,
        )
        try:
            os.replace(part_path, final_path)
            state_path.unlink(missing_ok=True)
        except OSError as exc:
            raise DocDlError(
                "filesystem_failure",
                "The verified document could not be committed to its final path",
                detail=str(exc),
            ) from exc
        return RetrievedDocument(
            path=final_path,
            media_type=verification.media_type,
            size=verification.size,
            page_count=verification.page_count,
            filename=final_path.name,
            effective_url=str(response.url),
            resumed=resumed,
        )

    @staticmethod
    def _partial_paths(url: str, output: Path | None) -> tuple[Path, Path]:
        if output is None:
            directory = Path.cwd()
        else:
            expanded = output.expanduser()
            if expanded.exists() and expanded.is_dir():
                directory = expanded
            elif expanded.suffix:
                directory = expanded.parent
            else:
                directory = expanded
        directory = directory.resolve()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DocDlError(
                "filesystem_failure",
                "The partial-download directory could not be prepared",
                detail=str(exc),
            ) from exc
        digest = hashlib.sha256(normalized_url(url).encode("utf-8")).hexdigest()[:20]
        part = directory / f".doc-dl-{digest}.part"
        return part, directory / f".doc-dl-{digest}.part.json"

    @staticmethod
    def _load_state(path: Path) -> ResumeState | None:
        try:
            payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return ResumeState(**payload)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _write_state(path: Path, state: ResumeState) -> None:
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        try:
            temporary.write_text(
                json.dumps(asdict(state), ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise DocDlError(
                "filesystem_failure",
                "Resume metadata could not be stored",
                detail=str(exc),
            ) from exc

    @staticmethod
    def _read_limited(response: httpx.Response, limit: int) -> bytes:
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes(CHUNK_SIZE):
            if not chunk:
                continue
            remaining = limit - size
            if remaining <= 0:
                break
            chunks.append(chunk[:remaining])
            size += min(len(chunk), remaining)
            if size >= limit:
                break
        return b"".join(chunks)

    @staticmethod
    def _decode_html(data: bytes, response: httpx.Response) -> str:
        encoding = response.encoding or "utf-8"
        try:
            return data.decode(encoding, errors="replace")
        except LookupError:
            return data.decode("utf-8", errors="replace")

    @staticmethod
    def _parse_content_range(value: str | None) -> tuple[int | None, int | None]:
        if not value:
            return None, None
        match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", value.strip())
        if not match:
            return None, None
        total = None if match.group(3) == "*" else int(match.group(3))
        return int(match.group(1)), total

    @staticmethod
    def _content_total(response: httpx.Response, start: int) -> int | None:
        value = response.headers.get("content-length")
        if not value or not value.isdigit():
            return None
        return start + int(value)

    @staticmethod
    def _validators_match(state: ResumeState | None, response: httpx.Response) -> bool:
        if not state:
            return False
        etag = response.headers.get("etag")
        last_modified = response.headers.get("last-modified")
        if state.etag:
            return bool(etag and etag == state.etag)
        if state.last_modified:
            return bool(last_modified and last_modified == state.last_modified)
        return False

    def _retry_delay(self, response: httpx.Response, attempt: int, deadline: float) -> float:
        retry_after = response.headers.get("retry-after")
        delay: float | None = None
        if retry_after:
            if retry_after.strip().isdigit():
                delay = float(retry_after.strip())
            else:
                try:
                    parsed = email.utils.parsedate_to_datetime(retry_after)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=UTC)
                    delay = max(0.0, (parsed - datetime.now(UTC)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    delay = None
        if delay is None:
            delay = self.retry_base_delay * (2**attempt) + random.uniform(0, 0.25)
        remaining = max(0.0, deadline - time.monotonic())
        return min(delay, 30.0, max(0.0, remaining - 0.1))
