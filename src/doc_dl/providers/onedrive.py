from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from doc_dl.providers.base import Provider

_SHORT_HOST = "1drv.ms"
_LIVE_HOSTS = {"onedrive.live.com", "skydrive.live.com"}


class OneDriveProvider(Provider):
    """Shared Microsoft OneDrive files.

    Older ``onedrive.live.com`` links carry an ``authkey`` and can be asked for
    the file directly. Newer personal share links cannot: Microsoft migrated
    personal OneDrive onto SharePoint, and those links resolve only to a
    JavaScript viewer that is handed the file URL after signing in. There is no
    URL that returns the bytes without a session, so nothing is rewritten and
    the reason is explained instead of failing obscurely.
    """

    name = "onedrive"
    supports_authentication = False
    supports_render = False
    document_host_suffixes = ("onedrive.live.com", "1drv.ms", "sharepoint.com")

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
        if (parts.hostname or "").casefold() not in _LIVE_HOSTS:
            return url

        query = [(key, value) for key, value in parse_qsl(parts.query) if key != "download"]
        keys = {key for key, _ in query}
        # Only the older authkey-bearing links have a working direct form.
        if "authkey" not in keys or not keys & {"resid", "id", "cid"}:
            return url
        query.append(("download", "1"))
        return urlunsplit((parts.scheme, parts.netloc, "/download", urlencode(query), ""))

    def access_hint(self) -> str | None:
        return (
            "Microsoft moved personal OneDrive share links onto SharePoint, and "
            "those links only hand out the file after a signed-in browser session. "
            "Open the link in your browser and use its Download button, or ask the "
            "owner for a direct file link."
        )
