from __future__ import annotations

import json
import sys
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

from doc_dl.redaction import redact_headers, redact_url


def _json_default(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return str(value)


def _redact_payload(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        if key and key.casefold() == "headers":
            return redact_headers({str(k): str(v) for k, v in value.items()})
        return {str(k): _redact_payload(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_payload(item, key) for item in value]
    if isinstance(value, tuple):
        return [_redact_payload(item, key) for item in value]
    if isinstance(value, str) and key and (key.endswith("url") or key == "url"):
        return redact_url(value)
    return value


class EventSink:
    def __init__(
        self,
        *,
        json_mode: bool = False,
        quiet: bool = False,
        verbose: bool = False,
        stream: TextIO | None = None,
        error_stream: TextIO | None = None,
    ) -> None:
        self.json_mode = json_mode
        self.quiet = quiet
        self.verbose = verbose
        self.stream = stream or sys.stdout
        self.error_stream = error_stream or sys.stderr

    def emit(self, event: str, **payload: Any) -> None:
        data = _redact_payload({"event": event, "version": 1, **payload})
        if self.json_mode:
            print(
                json.dumps(data, ensure_ascii=False, default=_json_default, sort_keys=True),
                file=self.stream,
                flush=True,
            )
            return

        if event == "error":
            message = str(data.get("message", data.get("error", "Unknown error")))
            print(f"ERROR: {message}", file=self.error_stream, flush=True)
            detail = data.get("detail")
            if detail:
                print(f"  {detail}", file=self.error_stream, flush=True)
            return

        if self.quiet and event != "complete":
            return
        if event in {"strategy", "candidate", "retry", "verification"} and not self.verbose:
            return

        message = data.get("message")
        if message:
            print(str(message), file=self.stream, flush=True)
        elif event == "download_progress":
            downloaded = data.get("downloaded", 0)
            total = data.get("total")
            if total:
                print(f"Downloaded {downloaded}/{total} bytes", file=self.stream, flush=True)
        elif event == "complete":
            print(str(data.get("path", "")), file=self.stream, flush=True)
