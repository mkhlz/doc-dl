from __future__ import annotations

import io
import os

from doc_dl.events import safe_print


def test_safe_print_replaces_characters_unsupported_by_console() -> None:
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="ascii")

    safe_print("message with ┌ unicode", file=stream)
    stream.flush()

    assert buffer.getvalue().decode("ascii") == f"message with ? unicode{os.linesep}"
