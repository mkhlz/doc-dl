from __future__ import annotations

import hashlib
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_HEADER_PARTS = (
    "authorization",
    "cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "api-key",
    "proxy-authorization",
)

SENSITIVE_QUERY_PARTS = (
    "token",
    "sig",
    "signature",
    "expires",
    "key",
    "auth",
    "credential",
    "policy",
)


def _sensitive(name: str, fragments: tuple[str, ...]) -> bool:
    lowered = name.casefold()
    return any(fragment in lowered for fragment in fragments)


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12]


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for name, value in headers.items():
        if _sensitive(name, SENSITIVE_HEADER_PARTS):
            redacted[name] = f"[redacted:{fingerprint(value)}]"
        else:
            redacted[name] = value
    return redacted


def redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "[invalid-url]"

    query: list[tuple[str, str]] = []
    for name, value in parse_qsl(parts.query, keep_blank_values=True):
        if _sensitive(name, SENSITIVE_QUERY_PARTS):
            query.append((name, f"[redacted:{fingerprint(value)}]"))
        else:
            query.append((name, value))

    hostname = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{hostname}{port}"
    if parts.username or parts.password:
        netloc = f"[redacted]@{netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), parts.fragment))
