from __future__ import annotations

from pathlib import Path

import pytest

from scripts.build_portable import TARGETS, select_chromium_directory
from scripts.write_checksums import CHECKSUM_FILE_NAME, sha256, write_checksums


def test_release_asset_names_follow_public_scheme() -> None:
    assert TARGETS == {
        "windows-x64": ("doc-dl_win", ".zip"),
        "linux-x64": ("doc-dl_linux", ".tar.gz"),
        "macos-x64": ("doc-dl_macos_x64", ".tar.gz"),
        "macos-arm64": ("doc-dl_macos_arm64", ".tar.gz"),
    }


def test_write_checksums_uses_release_filename(tmp_path: Path) -> None:
    asset = tmp_path / "doc-dl_linux.tar.gz"
    asset.write_bytes(b"portable release")
    (tmp_path / "SHA256SUMS").write_text("legacy\n", encoding="utf-8")

    checksum_file = write_checksums(tmp_path)

    assert checksum_file.name == CHECKSUM_FILE_NAME == "SHA2-256SUMS"
    assert checksum_file.read_text(encoding="utf-8") == f"{sha256(asset)}  {asset.name}\n"
    assert not (tmp_path / "SHA256SUMS").exists()


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
