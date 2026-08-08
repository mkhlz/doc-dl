from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from doc_dl.errors import DocDlError

_DEFAULT_CONCURRENCY = 3
_MAX_CONCURRENCY = 10


@dataclass(frozen=True, slots=True)
class BatchItem:
    url: str
    ok: bool
    path: Path | None
    detail: str


def read_batch_urls(path: Path) -> list[str]:
    """One URL per line; blank lines and '#' comments are skipped, so a
    file already used as notes doesn't need cleaning up first."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DocDlError(
            "invalid_arguments", f"Could not read the batch file: {path}", detail=str(exc)
        ) from exc
    urls = [
        stripped
        for line in text.splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]
    if not urls:
        raise DocDlError("invalid_arguments", f"The batch file has no URLs: {path}")
    return urls


def clamp_concurrency(value: int) -> int:
    return max(1, min(_MAX_CONCURRENCY, value))


def run_batch(
    urls: list[str],
    worker: Callable[[str], Path],
    *,
    concurrency: int = _DEFAULT_CONCURRENCY,
    on_result: Callable[[int, int, BatchItem], None] | None = None,
) -> list[BatchItem]:
    """Run `worker(url)` for every URL, several at a time.

    Each worker runs the exact same single-URL pipeline `doc-dl` always
    uses -- same retries, same reconstruction, same verification -- just
    with several running concurrently rather than one after another. All
    actual terminal output happens back on this thread as each result comes
    in, in the order results complete (not submission order), so two
    workers finishing at once can never interleave their output.
    """
    results: list[BatchItem] = []
    with ThreadPoolExecutor(max_workers=clamp_concurrency(concurrency)) as executor:
        futures = {executor.submit(worker, url): url for url in urls}
        for index, future in enumerate(as_completed(futures), start=1):
            url = futures[future]
            try:
                path = future.result()
                item = BatchItem(url=url, ok=True, path=path, detail=str(path))
            except DocDlError as exc:
                item = BatchItem(url=url, ok=False, path=None, detail=exc.message)
            except Exception as exc:
                item = BatchItem(url=url, ok=False, path=None, detail=str(exc))
            results.append(item)
            if on_result is not None:
                on_result(index, len(urls), item)
    return results
