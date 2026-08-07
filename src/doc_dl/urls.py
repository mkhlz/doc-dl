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


def registrable_domain(host: str) -> str:
    """The last two labels of a hostname, as a cheap stand-in for the
    registrable domain. Good enough to tell 'this site' from 'somebody
    else's site' without carrying a public-suffix dependency."""
    labels = [label for label in host.casefold().strip(".").split(".") if label]
    return ".".join(labels[-2:]) if len(labels) >= 2 else ".".join(labels)


def same_site(host: str, other: str) -> bool:
    if not host or not other:
        return False
    return registrable_domain(host) == registrable_domain(other)


def display_host(url: str) -> str:
    """The hostname as a person would name the site."""
    return host_of(url).removeprefix("www.")


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").casefold()
    except ValueError:
        return ""


def normalized_url(url: str) -> str:
    parts = urlsplit(validate_url(url))
    scheme = parts.scheme.casefold()
    host = (parts.hostname or "").casefold()
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    return urlunsplit((scheme, host, parts.path or "/", parts.query, ""))
