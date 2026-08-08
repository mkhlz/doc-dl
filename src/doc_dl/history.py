from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from doc_dl.config import StatePaths

HISTORY_FILENAME = "history.jsonl"


def history_path(state: StatePaths | None = None) -> Path:
    paths = state or StatePaths.discover()
    return paths.root / HISTORY_FILENAME


def append_history_entry(payload: dict[str, Any], *, state: StatePaths | None = None) -> None:
    """Record one download or archive result centrally, one JSON object per
    line, instead of dropping a `.doc-dl.json` file next to every output.

    Pasting a link and getting a file back is the whole point of the tool,
    so a folder of downloads staying exactly that -- just the files -- is
    kept even when metadata is being recorded. Failure here is swallowed:
    provenance history is a bonus, and must never be the reason an
    otherwise-successful download gets reported as failed.
    """
    try:
        paths = state or StatePaths.discover()
        paths.root.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
        encoded = line.encode("utf-8")
        with history_path(paths).open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass
