from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_portable import TARGETS, VARIANTS, select_chromium_directory, write_build_info
from scripts.write_checksums import CHECKSUM_FILE_NAME, sha256, write_checksums


def test_release_asset_names_follow_public_scheme() -> None:
    assert TARGETS == {
        "slim": {
            "windows-x64": ("doc-dl_win", ".zip"),
            "linux-x64": ("doc-dl_linux", ".tar.gz"),
            "macos-x64": ("doc-dl_macos_x64", ".tar.gz"),
            "macos-arm64": ("doc-dl_macos_arm64", ".tar.gz"),
        },
        "full": {
            "windows-x64": ("doc-dl_win_full", ".zip"),
            "linux-x64": ("doc-dl_linux_full", ".tar.gz"),
            "macos-x64": ("doc-dl_macos_x64_full", ".tar.gz"),
            "macos-arm64": ("doc-dl_macos_arm64_full", ".tar.gz"),
        },
    }
    assert VARIANTS == ("slim", "full")


def test_full_asset_names_are_slim_names_with_full_suffix() -> None:
    for target, (slim_stem, slim_ext) in TARGETS["slim"].items():
        full_stem, full_ext = TARGETS["full"][target]
        assert full_stem == f"{slim_stem}_full"
        assert full_ext == slim_ext


def test_write_build_info_marks_chromium_bundled_only_for_full(tmp_path: Path) -> None:
    write_build_info(tmp_path, "windows-x64", "slim", "0.1.2")
    slim_info = json.loads((tmp_path / "BUILD_INFO.json").read_text(encoding="utf-8"))
    assert slim_info["variant"] == "slim"
    assert slim_info["chromium_bundled"] is False

    write_build_info(tmp_path, "windows-x64", "full", "0.1.2")
    full_info = json.loads((tmp_path / "BUILD_INFO.json").read_text(encoding="utf-8"))
    assert full_info["variant"] == "full"
    assert full_info["chromium_bundled"] is True


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
