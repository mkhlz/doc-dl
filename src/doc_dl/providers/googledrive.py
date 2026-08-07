from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from doc_dl.providers.base import Provider

_DRIVE_HOSTS = {"drive.google.com", "docs.google.com"}
_FILE_PATH = re.compile(r"^/file/d/(?P<id>[^/]+)", re.IGNORECASE)


class GoogleDriveProvider(Provider):
    """Shared Google Drive files.

    A shared link points at a viewer page rather than the file, so the file id
    is lifted out of the URL and pointed at Drive's own download endpoint. That
    keeps the download on the plain HTTP path instead of escalating to a
    browser that would only find the viewer chrome.
    """

    name = "googledrive"
    supports_authentication = False
    supports_render = False
    document_host_suffixes = ("googleusercontent.com", "usercontent.google.com")

    def match(self, url: str) -> int:
        return 100 if self._file_id(url) else 0

    def normalize(self, url: str) -> str:
        file_id, resource_key = self._file_parts(url)
        if not file_id:
            return url
        query: list[tuple[str, str]] = [("export", "download"), ("id", file_id)]
        if resource_key:
            query.append(("resourcekey", resource_key))
        return urlunsplit(("https", "drive.google.com", "/uc", urlencode(query), ""))

    @staticmethod
    def _file_parts(url: str) -> tuple[str | None, str | None]:
        try:
            parts = urlsplit(url)
        except ValueError:
            return None, None
        if (parts.hostname or "").casefold() not in _DRIVE_HOSTS:
            return None, None

        query = parse_qs(parts.query)
        match = _FILE_PATH.match(parts.path)
        file_id = match.group("id") if match else None
        if not file_id:
            # /uc and /open style links carry the id in the query instead.
            ids = query.get("id")
            file_id = ids[0] if ids else None
        if not file_id:
            return None, None

        keys = query.get("resourcekey") or query.get("resourceKey")
        return file_id, keys[0] if keys else None

    @classmethod
    def _file_id(cls, url: str) -> str | None:
        return cls._file_parts(url)[0]

    def access_hint(self) -> str | None:
        return (
            "This Google Drive file is not shared publicly. Ask the owner to set it "
            "to 'Anyone with the link', and keep any resourcekey value in the URL, "
            "which older shared files require."
        )
