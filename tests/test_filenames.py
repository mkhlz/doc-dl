from __future__ import annotations

from pathlib import Path

import pytest

from doc_dl.errors import DocDlError
from doc_dl.filenames import (
    apply_filename_template,
    filename_from_content_disposition,
    resolve_output_path,
    sanitize_filename,
)


def test_sanitize_filename_handles_windows_reserved_and_invalid_characters() -> None:
    assert sanitize_filename("CON.pdf") == "_CON.pdf"
    assert sanitize_filename('report<>:"/\\|?*.pdf') == "report________.pdf"


def test_sanitize_filename_drops_commas_and_quote_marks() -> None:
    assert sanitize_filename("Gravely injured, 'a voice in the dark'.pdf") == (
        "Gravely injured a voice in the dark.pdf"
    )
    assert sanitize_filename("She said “hello”, then left.pdf") == "She said hello then left.pdf"


def test_content_disposition_prefers_rfc5987_filename() -> None:
    value = "attachment; filename=old.pdf; filename*=UTF-8''new%20name.pdf"
    assert filename_from_content_disposition(value) == "new name.pdf"


def test_filename_template_supports_contract_fields() -> None:
    assert (
        apply_filename_template(
            "{provider}-{title}.{ext}",
            "Annual Report.pdf",
            provider="generic",
        )
        == "generic-Annual Report.pdf"
    )


def test_filename_template_rejects_unknown_field() -> None:
    with pytest.raises(DocDlError, match="template") as raised:
        apply_filename_template("{unknown}.pdf", "report.pdf", provider="generic")
    assert raised.value.identifier == "invalid_arguments"


def test_resolve_output_directory_and_collision(tmp_path: Path) -> None:
    result = resolve_output_path(tmp_path, "report.pdf", overwrite=False)
    assert result == tmp_path / "report.pdf"
    result.write_bytes(b"existing")
    with pytest.raises(DocDlError) as raised:
        resolve_output_path(tmp_path, "report.pdf", overwrite=False)
    assert raised.value.identifier == "output_exists"
