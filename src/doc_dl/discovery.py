from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from doc_dl.models import CandidateKind, DocumentCandidate
from doc_dl.verify import DOCUMENT_EXTENSIONS, base_media_type

_DOCUMENT_URL = re.compile(
    r"https?://[^\s\"'<>]+\.(?:pdf|epub|rtf|docx?|pptx?|xlsx?|odt|odp|ods)(?:\?[^\s\"'<>]*)?",
    flags=re.IGNORECASE,
)


class _DocumentHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str | None, str]] = []
        self.metadata_urls: list[tuple[str, str | None, str]] = []
        self._json_ld_depth = 0
        self._json_ld_chunks: list[str] = []
        self.json_ld: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): value for name, value in attrs}
        lowered = tag.casefold()
        if lowered in {"a", "link"} and values.get("href"):
            self.links.append(
                (
                    values["href"] or "",
                    values.get("type"),
                    values.get("download") or values.get("title") or lowered,
                )
            )
        if lowered in {"iframe", "embed", "source"} and values.get("src"):
            self.links.append((values["src"] or "", values.get("type"), lowered))
        if lowered == "object" and values.get("data"):
            self.links.append((values["data"] or "", values.get("type"), lowered))
        if lowered == "meta" and values.get("content"):
            label = values.get("property") or values.get("name") or "meta"
            if any(token in label.casefold() for token in ("url", "document", "download", "pdf")):
                self.metadata_urls.append((values["content"] or "", None, label))
        if lowered == "script" and (values.get("type") or "").casefold() == "application/ld+json":
            self._json_ld_depth += 1
            self._json_ld_chunks = []

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._json_ld_depth:
            self.json_ld.append("".join(self._json_ld_chunks))
            self._json_ld_depth = 0
            self._json_ld_chunks = []

    def handle_data(self, data: str) -> None:
        if self._json_ld_depth:
            self._json_ld_chunks.append(data)


def _walk_json(value: Any, label: str = "json-ld") -> list[tuple[str, str | None, str]]:
    found: list[tuple[str, str | None, str]] = []
    if isinstance(value, dict):
        media_type = value.get("encodingFormat") or value.get("fileFormat")
        for key, item in value.items():
            if key in {"contentUrl", "downloadUrl", "url", "embedUrl"} and isinstance(item, str):
                found.append((item, str(media_type) if media_type else None, f"{label}:{key}"))
            found.extend(_walk_json(item, f"{label}:{key}"))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_json(item, label))
    return found


def _score_candidate(url: str, media_type: str | None, source: str) -> int:
    score = 30
    extension = urlsplit(url).path.casefold()
    if any(extension.endswith(item) for item in DOCUMENT_EXTENSIONS):
        score += 35
    if base_media_type(media_type):
        score += 20
    source_lower = source.casefold()
    if any(token in source_lower for token in ("download", "contenturl", "document", "pdf")):
        score += 15
    return min(score, 100)


def discover_document_candidates(
    html: str,
    base_url: str,
    *,
    provider: str = "generic",
    strategy: str = "static-html",
) -> list[DocumentCandidate]:
    parser = _DocumentHTMLParser()
    parser.feed(html)

    raw = [*parser.links, *parser.metadata_urls]
    for block in parser.json_ld:
        try:
            raw.extend(_walk_json(json.loads(block)))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    raw.extend((match.group(0), None, "embedded-url") for match in _DOCUMENT_URL.finditer(html))

    candidates: list[DocumentCandidate] = []
    seen: set[str] = set()
    for raw_url, media_type, source in raw:
        absolute = urljoin(base_url, raw_url.strip())
        parts = urlsplit(absolute)
        if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
            continue
        dedupe = absolute.split("#", 1)[0]
        if dedupe in seen:
            continue
        seen.add(dedupe)
        score = _score_candidate(dedupe, media_type, source)
        extension = PathLikeSuffix.from_url(dedupe)
        if score < 60 and extension not in DOCUMENT_EXTENSIONS:
            continue
        candidates.append(
            DocumentCandidate(
                url=dedupe,
                strategy=strategy,
                provider=provider,
                kind=CandidateKind.ORIGINAL,
                media_type=base_media_type(media_type),
                confidence=score,
                headers={"Referer": base_url},
                metadata={"source": source},
            )
        )
    candidates.sort(key=lambda item: item.confidence, reverse=True)
    return candidates


class PathLikeSuffix:
    @staticmethod
    def from_url(url: str) -> str:
        path = urlsplit(url).path
        leaf = path.rsplit("/", 1)[-1]
        if "." not in leaf:
            return ""
        return f".{leaf.rsplit('.', 1)[-1].casefold()}"
