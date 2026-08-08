from __future__ import annotations

from pathlib import Path

import pytest

from doc_dl.batch import BatchItem, clamp_concurrency, read_batch_urls, run_batch
from doc_dl.cli import run
from doc_dl.errors import DocDlError
from tests.fixture_server import FixtureServer


def test_read_batch_urls_skips_blanks_and_comments(tmp_path: Path) -> None:
    batch_file = tmp_path / "urls.txt"
    batch_file.write_text(
        "https://example.com/a\n\n# a comment\n  https://example.com/b  \n",
        encoding="utf-8",
    )
    assert read_batch_urls(batch_file) == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_read_batch_urls_rejects_an_empty_file(tmp_path: Path) -> None:
    batch_file = tmp_path / "urls.txt"
    batch_file.write_text("# nothing but comments\n", encoding="utf-8")
    with pytest.raises(DocDlError) as raised:
        read_batch_urls(batch_file)
    assert raised.value.identifier == "invalid_arguments"


def test_clamp_concurrency_bounds_to_one_and_ten() -> None:
    assert clamp_concurrency(0) == 1
    assert clamp_concurrency(-5) == 1
    assert clamp_concurrency(100) == 10
    assert clamp_concurrency(4) == 4


def test_run_batch_processes_urls_concurrently_and_reports_failures() -> None:
    def worker(url: str) -> Path:
        if url.endswith("bad"):
            raise DocDlError("network_failure", f"could not reach {url}")
        return Path(f"/tmp/{url.rsplit('/', 1)[-1]}.pdf")

    urls = ["http://x/good1", "http://x/bad", "http://x/good2"]
    seen: list[BatchItem] = []
    results = run_batch(urls, worker, concurrency=2, on_result=lambda i, t, item: seen.append(item))

    assert len(results) == 3
    assert len(seen) == 3
    ok_urls = {item.url for item in results if item.ok}
    failed_urls = {item.url for item in results if not item.ok}
    assert ok_urls == {"http://x/good1", "http://x/good2"}
    assert failed_urls == {"http://x/bad"}
    failed_item = next(item for item in results if not item.ok)
    assert "could not reach" in failed_item.detail


def test_download_batch_end_to_end(
    fixture_server: FixtureServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOC_DL_STATE_DIR", str(tmp_path / "state"))
    batch_file = tmp_path / "urls.txt"
    batch_file.write_text(
        f"{fixture_server.url('/files/sample.pdf')}\n{fixture_server.url('/errors/corrupt.pdf')}\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "output"
    exit_code = run(
        [
            "--batch",
            str(batch_file),
            "--concurrency",
            "2",
            "-o",
            str(output_dir),
            "--quiet",
        ]
    )
    # One URL succeeds and one is a genuinely corrupt PDF; the batch should
    # report a nonzero exit for the failure without losing the success.
    assert exit_code == 1
    assert list(output_dir.glob("*.pdf"))


def test_batch_and_url_together_is_rejected(tmp_path: Path) -> None:
    batch_file = tmp_path / "urls.txt"
    batch_file.write_text("https://example.com/a\n", encoding="utf-8")
    exit_code = run(["https://example.com/b", "--batch", str(batch_file), "--quiet"])
    assert exit_code == 2


def test_archive_batch_rejects_missing_file(tmp_path: Path) -> None:
    exit_code = run(["archive", "--batch", str(tmp_path / "missing.txt"), "--quiet"])
    assert exit_code == 2
