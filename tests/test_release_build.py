from __future__ import annotations

from pathlib import Path

import pytest

from scripts.build_portable import select_chromium_directory


def test_select_chromium_directory_ignores_optional_tools(tmp_path: Path) -> None:
    expected = tmp_path / "chromium-1234"
    expected.mkdir()
    (tmp_path / "chromium_headless_shell-1234").mkdir()
    (tmp_path / "ffmpeg-1011").mkdir()

    assert select_chromium_directory(tmp_path) == expected


def test_select_chromium_directory_uses_newest_revision(tmp_path: Path) -> None:
    (tmp_path / "chromium-1200").mkdir()
    expected = tmp_path / "chromium-1234"
    expected.mkdir()

    assert select_chromium_directory(tmp_path) == expected


def test_select_chromium_directory_requires_full_browser(tmp_path: Path) -> None:
    (tmp_path / "chromium_headless_shell-1234").mkdir()

    with pytest.raises(RuntimeError, match="No full Chromium installation"):
        select_chromium_directory(tmp_path)
