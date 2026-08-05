from __future__ import annotations

from pathlib import Path

from doc_dl.discovery import discover_document_candidates

FIXTURES = Path(__file__).parent / "fixtures" / "site"


def test_discovers_static_download_link() -> None:
    html = (FIXTURES / "static-link.html").read_text(encoding="utf-8")
    candidates = discover_document_candidates(html, "https://example.test/site/static-link.html")
    assert candidates[0].url == "https://example.test/files/sample.pdf"
    assert candidates[0].confidence >= 80


def test_discovers_jsonld_content_url() -> None:
    html = (FIXTURES / "jsonld-link.html").read_text(encoding="utf-8")
    candidates = discover_document_candidates(html, "https://example.test/site/jsonld-link.html")
    assert any(candidate.url == "https://example.test/files/sample.pdf" for candidate in candidates)


def test_ignores_javascript_and_blob_urls() -> None:
    html = '<a href="javascript:alert(1)">x</a><iframe src="blob:abc"></iframe>'
    assert discover_document_candidates(html, "https://example.test/") == []
