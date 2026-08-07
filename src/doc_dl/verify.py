from __future__ import annotations

import mimetypes
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from doc_dl.errors import DocDlError

MEDIA_TYPE_EXTENSIONS: dict[str, str] = {
    "application/pdf": ".pdf",
    "application/epub+zip": ".epub",
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
}

# Plain-text responses are far more often telemetry beacons, analytics pings,
# or API chatter than real documents, so they are not treated as document-like
# on their content type alone the way binary document formats are.
TEXT_MEDIA_TYPES = {"text/plain", "text/markdown", "text/csv"}

DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".epub",
    ".rtf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".odt",
    ".odp",
    ".ods",
    ".txt",
    ".md",
    ".csv",
}


@dataclass(frozen=True, slots=True)
class VerificationResult:
    media_type: str
    size: int
    page_count: int | None = None
    encrypted: bool = False


def base_media_type(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(";", 1)[0].strip().casefold() or None


def extension_for_media_type(media_type: str | None) -> str | None:
    normalized = base_media_type(media_type)
    if not normalized:
        return None
    return MEDIA_TYPE_EXTENSIONS.get(normalized) or mimetypes.guess_extension(normalized)


def looks_like_html(data: bytes) -> bool:
    sample = data[:4096].lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    html_markers = (
        b"<!doctype html",
        b"<html",
        b"<head",
        b"<body",
        b'<?xml version="1.0"?><html',
    )
    return any(sample.startswith(marker) for marker in html_markers)


def response_looks_document_like(
    *,
    url: str,
    media_type: str | None,
    content_disposition: str | None,
) -> bool:
    normalized_type = base_media_type(media_type)
    attachment = bool(content_disposition and "attachment" in content_disposition.casefold())
    extension = Path(urlsplit(url).path).suffix.casefold()
    if normalized_type in TEXT_MEDIA_TYPES:
        # A text/plain body needs corroborating evidence that it is really a
        # document: an explicit attachment disposition, or a document
        # extension on the URL. Otherwise endpoints such as Cloudflare's
        # /cdn-cgi/trace get mistaken for the document being downloaded.
        return attachment or extension in DOCUMENT_EXTENSIONS
    if normalized_type in MEDIA_TYPE_EXTENSIONS:
        return True
    if attachment:
        return True
    return extension in DOCUMENT_EXTENSIONS


_GENERIC_MEDIA_TYPES = {None, "application/octet-stream", "application/binary"}
_SUFFIX_MEDIA_TYPES = {".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv"}


def media_type_for_download(media_type: str | None, filename: str) -> str | None:
    """Resolve a usefully specific media type for a download.

    Servers routinely hand back a placeholder such as application/octet-stream
    for text attachments (Google Drive does this), which later reads as an
    unrecognised document. The filename the server itself supplied is a better
    signal in that case.
    """
    if base_media_type(media_type) not in _GENERIC_MEDIA_TYPES:
        return media_type
    return _SUFFIX_MEDIA_TYPES.get(Path(filename).suffix.casefold(), media_type)


_REPLACEABLE_SUFFIXES = {"", ".bin", ".download", ".php", ".aspx", ".html", ".htm"}


def ensure_document_extension(filename: str, media_type: str) -> str:
    expected = extension_for_media_type(media_type)
    if not expected:
        return filename
    suffix = Path(filename).suffix.casefold()
    if suffix == expected:
        return filename
    # A document extension that contradicts the verified content is worse than
    # no extension at all: SlideShare titles carry the uploader's original
    # ".pptx" even though what is delivered is a PDF, and the file would then
    # fail to open. The verified media type wins.
    if suffix in DOCUMENT_EXTENSIONS or suffix in _REPLACEABLE_SUFFIXES:
        stem = filename[: -len(suffix)] if suffix else filename
        return f"{stem}{expected}"
    return f"{filename}{expected}"


def _verify_pdf(path: Path, expected_pages: int | None) -> VerificationResult:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(path), strict=False)
        encrypted = bool(reader.is_encrypted)
        page_count = len(reader.pages)
    except (OSError, PdfReadError, ValueError, TypeError) as exc:
        raise DocDlError(
            "corrupt_document",
            "The downloaded PDF is corrupt or incomplete",
            detail=str(exc),
        ) from exc

    if page_count < 1:
        raise DocDlError("corrupt_document", "The downloaded PDF contains no pages")
    if expected_pages is not None and page_count != expected_pages:
        raise DocDlError(
            "render_incomplete",
            f"Expected {expected_pages} PDF pages but produced {page_count}",
        )
    return VerificationResult(
        media_type="application/pdf",
        size=path.stat().st_size,
        page_count=page_count,
        encrypted=encrypted,
    )


def _verify_zip_document(path: Path) -> VerificationResult | None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" in names:
                if any(name.startswith("word/") for name in names):
                    media_type = (
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    )
                elif any(name.startswith("ppt/") for name in names):
                    media_type = (
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                    )
                elif any(name.startswith("xl/") for name in names):
                    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                else:
                    return None
                bad_member = archive.testzip()
                if bad_member:
                    raise DocDlError(
                        "corrupt_document",
                        f"The document archive contains a corrupt member: {bad_member}",
                    )
                return VerificationResult(media_type=media_type, size=path.stat().st_size)

            if "mimetype" in names:
                declared = archive.read("mimetype").decode("ascii", errors="replace").strip()
                if declared in MEDIA_TYPE_EXTENSIONS:
                    bad_member = archive.testzip()
                    if bad_member:
                        raise DocDlError(
                            "corrupt_document",
                            f"The document archive contains a corrupt member: {bad_member}",
                        )
                    return VerificationResult(media_type=declared, size=path.stat().st_size)
    except zipfile.BadZipFile as exc:
        raise DocDlError(
            "corrupt_document",
            "The downloaded document archive is corrupt or incomplete",
            detail=str(exc),
        ) from exc
    return None


def verify_document(
    path: Path,
    *,
    media_type_hint: str | None = None,
    expected_pages: int | None = None,
) -> VerificationResult:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            prefix = handle.read(8192)
    except OSError as exc:
        raise DocDlError(
            "filesystem_failure",
            "The downloaded file could not be read for verification",
            detail=str(exc),
        ) from exc

    if size <= 0:
        raise DocDlError("verification_failed", "The downloaded file is empty")
    if looks_like_html(prefix):
        raise DocDlError(
            "unexpected_content",
            "The server returned an HTML page instead of a document",
        )
    if prefix.startswith(b"%PDF-"):
        return _verify_pdf(path, expected_pages)
    if prefix.startswith(b"PK\x03\x04"):
        zip_result = _verify_zip_document(path)
        if zip_result:
            return zip_result
    if prefix.startswith(b"{\\rtf"):
        return VerificationResult(media_type="application/rtf", size=size)
    if prefix.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        hint = base_media_type(media_type_hint)
        if hint in {
            "application/msword",
            "application/vnd.ms-excel",
            "application/vnd.ms-powerpoint",
        }:
            return VerificationResult(media_type=hint, size=size)

    hint = base_media_type(media_type_hint)
    suffix = path.suffix.casefold()
    if hint in {"text/plain", "text/markdown", "text/csv"} and b"\x00" not in prefix:
        return VerificationResult(media_type=hint, size=size)
    if suffix in {".txt", ".md", ".csv"} and b"\x00" not in prefix:
        return VerificationResult(
            media_type={".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv"}[suffix],
            size=size,
        )

    raise DocDlError(
        "unexpected_content",
        "The downloaded response is not a recognized document",
        detail=f"Content type hint: {media_type_hint or 'none'}",
    )
