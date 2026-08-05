from __future__ import annotations

from doc_dl.redaction import redact_headers, redact_url


def test_redact_headers_removes_credentials() -> None:
    result = redact_headers(
        {
            "Authorization": "Bearer secret",
            "Cookie": "session=abc",
            "Content-Type": "application/pdf",
        }
    )
    assert result["Authorization"].startswith("[redacted:")
    assert result["Cookie"].startswith("[redacted:")
    assert result["Content-Type"] == "application/pdf"
    assert "secret" not in str(result)


def test_redact_url_preserves_names_but_removes_signed_values() -> None:
    result = redact_url("https://cdn.example.test/file.pdf?token=secret&part=1&signature=abc")
    assert "token=" in result
    assert "signature=" in result
    assert "part=1" in result
    assert "secret" not in result
    assert "abc" not in result
