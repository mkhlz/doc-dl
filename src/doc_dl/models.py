from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class Provenance(StrEnum):
    ORIGINAL = "original"
    EXPORTED = "exported"
    RECONSTRUCTED = "reconstructed"
    PRINTED = "printed"


class CandidateKind(StrEnum):
    ORIGINAL = "original"
    BROWSER_DOWNLOAD = "browser-download"
    NETWORK_RESPONSE = "network-response"
    RENDER_PLAN = "render-plan"


class StrategyStatus(StrEnum):
    SKIPPED = "skipped"
    FAILED = "failed"
    SUCCEEDED = "succeeded"


@dataclass(slots=True)
class DocumentCandidate:
    url: str
    strategy: str
    provider: str = "generic"
    kind: CandidateKind = CandidateKind.ORIGINAL
    media_type: str | None = None
    filename: str | None = None
    size: int | None = None
    confidence: int = 50
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StrategyRecord:
    strategy: str
    status: StrategyStatus
    reason_code: str
    detail: str
    elapsed_ms: int


@dataclass(slots=True)
class DownloadRequest:
    url: str
    output: Path | None = None
    filename_template: str | None = None
    original_only: bool = False
    allow_render: bool = True
    forced_provider: str | None = None
    profile: str = "default"
    browser_enabled: bool = True
    resume: bool = True
    overwrite: bool = False
    timeout_seconds: float = 180.0
    retries: int = 3
    write_metadata: bool = False


@dataclass(slots=True)
class DownloadResult:
    path: Path
    filename: str
    media_type: str
    size: int
    provenance: Provenance
    provider: str
    source_url: str
    elapsed_ms: int
    page_count: int | None = None
    strategies: list[StrategyRecord] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ImagePageSet:
    """A document reconstructable by fetching one already-known image URL
    per page directly, with no browser involved (e.g. SlideShare slides)."""

    title: str | None
    image_urls: list[str]


@dataclass(slots=True)
class BrowserDiscovery:
    candidates: list[DocumentCandidate] = field(default_factory=list)
    downloaded_files: list[tuple[Path, str | None, str | None]] = field(default_factory=list)
    rendered_file: Path | None = None
    rendered_provenance: Provenance | None = None
    rendered_filename: str | None = None
    rendered_page_count: int | None = None
    authentication_required: bool = False
    access_denied: bool = False
    title: str | None = None
    final_url: str | None = None
