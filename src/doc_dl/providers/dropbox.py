from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from doc_dl.providers.base import Provider

_DROPBOX_HOSTS = {"www.dropbox.com", "dropbox.com"}


class DropboxProvider(Provider):
    """Shared Dropbox files.

    A share link opens a preview page by default. Dropbox serves the file
    itself from the same URL when ``dl=1`` is set, so only that one parameter
    has to change; every other part of the link, including the ``rlkey`` that
    newer share links require, is preserved.
    """

    name = "dropbox"
    supports_authentication = False
    supports_render = False
    document_host_suffixes = ("dropboxusercontent.com",)

    def match(self, url: str) -> int:
        try:
            parts = urlsplit(url)
        except ValueError:
            return 0
        host = (parts.hostname or "").casefold()
        if host in _DROPBOX_HOSTS:
            return 100
        # Direct content links already point at the file.
        return 90 if host.endswith("dropboxusercontent.com") else 0

    def normalize(self, url: str) -> str:
        try:
            parts = urlsplit(url)
        except ValueError:
            return url
        if (parts.hostname or "").casefold() not in _DROPBOX_HOSTS:
            return url

        query = [(key, value) for key, value in parse_qsl(parts.query) if key != "dl"]
        query.append(("dl", "1"))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
