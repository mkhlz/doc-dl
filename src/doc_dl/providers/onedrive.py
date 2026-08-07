from __future__ import annotations

import base64
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from doc_dl.providers.base import Provider

_SHORT_HOST = "1drv.ms"
_LIVE_HOSTS = {"onedrive.live.com", "skydrive.live.com"}


def _share_token(url: str) -> str:
    """Encode a share link the way OneDrive's shares API expects.

    The API takes the whole link, base64url-encoded, stripped of padding and
    prefixed with "u!".
    """
    encoded = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii")
    return f"u!{encoded.rstrip('=')}"


class OneDriveProvider(Provider):
    """Shared Microsoft OneDrive files.

    Personal share links (``1drv.ms`` short links and ``onedrive.live.com``
    pages) open a viewer. OneDrive's public shares API returns the file itself
    for any such link, so the link is handed to that endpoint instead. Business
    and SharePoint links use a different host and are left alone rather than
    rewritten into something that would not resolve.
    """

    name = "onedrive"
    supports_authentication = False
    supports_render = False
    document_host_suffixes = ("api.onedrive.com", "onedrive.live.com", "1drv.ms")

    def match(self, url: str) -> int:
        try:
            parts = urlsplit(url)
        except ValueError:
            return 0
        host = (parts.hostname or "").casefold()
        return 100 if host == _SHORT_HOST or host in _LIVE_HOSTS else 0

    def normalize(self, url: str) -> str:
        try:
            parts = urlsplit(url)
        except ValueError:
            return url
        host = (parts.hostname or "").casefold()

        if host == _SHORT_HOST:
            return f"https://api.onedrive.com/v1.0/shares/{_share_token(url)}/root/content"

        if host not in _LIVE_HOSTS:
            return url

        # A live.com link already carrying an id can ask for the file directly.
        query = [(key, value) for key, value in parse_qsl(parts.query) if key != "download"]
        if any(key in {"resid", "id", "cid"} for key, _ in query):
            query.append(("download", "1"))
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))

        return f"https://api.onedrive.com/v1.0/shares/{_share_token(url)}/root/content"
