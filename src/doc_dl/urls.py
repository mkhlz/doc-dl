from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from doc_dl.errors import DocDlError


def validate_url(url: str) -> str:
    candidate = url.strip()
    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise DocDlError(
            "invalid_arguments",
            "The supplied URL is invalid",
            detail=str(exc),
        ) from exc

    if parts.scheme.casefold() not in {"http", "https"}:
        raise DocDlError(
            "invalid_arguments",
            "Only HTTP and HTTPS document URLs are supported",
        )
    if not parts.hostname:
        raise DocDlError("invalid_arguments", "The supplied URL has no hostname")
    try:
        port = parts.port
    except ValueError as exc:
        raise DocDlError(
            "invalid_arguments",
            "The supplied URL has an invalid port",
            detail=str(exc),
        ) from exc
    if port is not None and not 0 < port <= 65535:
        raise DocDlError("invalid_arguments", "The supplied URL has an invalid port")
    if parts.username or parts.password:
        raise DocDlError(
            "invalid_arguments",
            "Credentials embedded in a URL are not accepted",
            detail="Use an isolated authenticated profile instead.",
        )
    return candidate


def normalized_url(url: str) -> str:
    parts = urlsplit(validate_url(url))
    scheme = parts.scheme.casefold()
    host = (parts.hostname or "").casefold()
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    return urlunsplit((scheme, host, parts.path or "/", parts.query, ""))
