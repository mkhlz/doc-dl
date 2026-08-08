from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

from doc_dl.errors import DocDlError

_INVALID_FILENAME_CHARS = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")
# Commas and quote marks (straight or curly) are all valid filename
# characters, but read as clutter in a title-derived name -- dropped
# entirely rather than swapped for an underscore like the truly invalid
# characters above.
_DECORATIVE_MARKS = re.compile("[,'\"‘’“”]")  # noqa: RUF001 -- real curly quote marks
_MULTIPLE_SPACES = re.compile(r"\s+")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def sanitize_filename(value: str | None, fallback: str = "document") -> str:
    text = unicodedata.normalize("NFKC", unquote(value or ""))
    text = _DECORATIVE_MARKS.sub("", text)
    text = _INVALID_FILENAME_CHARS.sub("_", text)
    text = _MULTIPLE_SPACES.sub(" ", text).strip(" .")
    if not text:
        text = fallback

    stem, suffix = os.path.splitext(text)
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    text = f"{stem}{suffix}"

    encoded = text.encode("utf-8")
    if len(encoded) > 240:
        suffix_bytes = suffix.encode("utf-8")
        available = max(1, 240 - len(suffix_bytes))
        truncated = stem.encode("utf-8")[:available]
        while truncated:
            try:
                stem = truncated.decode("utf-8")
                break
            except UnicodeDecodeError:
                truncated = truncated[:-1]
        text = f"{stem.rstrip(' .')}{suffix}"
    return text or fallback


def filename_from_url(url: str, fallback: str = "document") -> str:
    path = urlsplit(url).path.rstrip("/")
    raw = path.rsplit("/", 1)[-1] if path else fallback
    return sanitize_filename(raw, fallback)


def filename_from_content_disposition(value: str | None) -> str | None:
    if not value:
        return None

    extended = re.search(r"filename\*\s*=\s*([^;]+)", value, flags=re.IGNORECASE)
    if extended:
        token = extended.group(1).strip().strip('"')
        if "''" in token:
            _charset, token = token.split("''", 1)
        return sanitize_filename(unquote(token))

    plain = re.search(r"filename\s*=\s*(\"(?:[^\"]|\\\")*\"|[^;]+)", value, re.IGNORECASE)
    if plain:
        token = plain.group(1).strip().strip('"').replace('\\"', '"')
        return sanitize_filename(token)
    return None


def apply_filename_template(
    template: str | None,
    default_filename: str,
    *,
    provider: str,
) -> str:
    if not template:
        return sanitize_filename(default_filename)
    default_path = Path(default_filename)
    values = {
        "title": default_path.stem or "document",
        "ext": default_path.suffix.lstrip("."),
        "provider": provider,
        "filename": default_filename,
    }
    try:
        rendered = template.format_map(values)
    except (KeyError, ValueError) as exc:
        raise DocDlError(
            "invalid_arguments",
            "The filename template is invalid",
            detail=(f"{exc}. Available fields: {{title}}, {{ext}}, {{provider}}, {{filename}}."),
        ) from exc
    return sanitize_filename(rendered, default_filename)


def resolve_output_path(
    output: Path | None,
    filename: str,
    *,
    overwrite: bool,
) -> Path:
    safe_name = sanitize_filename(filename)
    if output is None:
        result = Path.cwd() / safe_name
    else:
        expanded = output.expanduser()
        output_text = str(output)
        is_directory_hint = output_text.endswith(("/", "\\")) or not expanded.suffix
        if (expanded.exists() and expanded.is_dir()) or is_directory_hint:
            result = expanded / safe_name
        else:
            result = expanded

    try:
        result = result.resolve()
        result.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DocDlError(
            "filesystem_failure",
            "The output directory could not be prepared",
            detail=str(exc),
        ) from exc

    if result.exists() and not overwrite:
        raise DocDlError(
            "output_exists",
            f"Output already exists: {result}",
            detail="Use --overwrite to replace it.",
        )
    return result
