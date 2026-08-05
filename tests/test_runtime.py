from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from doc_dl.config import StatePaths
from doc_dl.errors import DocDlError
from doc_dl.runtime import (
    build_variant,
    bundled_offline_browsers_dir,
    configure_browsers_path,
    effective_browsers_path,
    install_chromium,
    is_chromium_installed,
    uninstall_chromium,
)


def test_source_install_does_not_change_playwright_path(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    assert configure_browsers_path() is None
    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ
    assert build_variant() == "source"


def test_frozen_full_build_uses_browser_beside_executable(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "doc-dl.exe"
    browser_root = tmp_path / "ms-playwright"
    browser_root.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    assert bundled_offline_browsers_dir() == browser_root
    assert configure_browsers_path() == browser_root
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(browser_root)
    assert build_variant() == "full"


def test_frozen_slim_build_defaults_to_state_directory(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "doc-dl.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    state = StatePaths(tmp_path / "state")

    assert bundled_offline_browsers_dir() is None
    resolved = configure_browsers_path(state)
    assert resolved == state.browsers()
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(state.browsers())
    assert build_variant() == "slim"


def test_frozen_install_preserves_explicit_browser_path(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "doc-dl"
    browser_root = tmp_path / "ms-playwright"
    browser_root.mkdir()
    explicit = tmp_path / "custom-browsers"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(explicit))

    assert configure_browsers_path() == explicit
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(explicit)


def test_is_chromium_installed_reflects_executable_presence(monkeypatch) -> None:
    monkeypatch.setattr("doc_dl.runtime.chromium_executable_path", lambda: None)
    assert is_chromium_installed() is False


def test_is_chromium_installed_true_when_executable_exists(tmp_path: Path, monkeypatch) -> None:
    fake_executable = tmp_path / "chrome.exe"
    fake_executable.write_bytes(b"binary")
    monkeypatch.setattr("doc_dl.runtime.chromium_executable_path", lambda: fake_executable)
    assert is_chromium_installed() is True


def test_install_chromium_raises_when_playwright_missing(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "playwright._impl._driver":
            raise ImportError("no playwright")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(DocDlError) as raised:
        install_chromium()
    assert raised.value.identifier == "browser_unavailable"


def test_install_chromium_raises_when_install_did_not_produce_executable(
    tmp_path: Path, monkeypatch
) -> None:
    state = StatePaths(tmp_path / "state")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "doc-dl.exe"))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    class FakeCompleted:
        returncode = 0

    monkeypatch.setattr(
        "playwright._impl._driver.compute_driver_executable",
        lambda: ("node", "cli.js"),
    )
    monkeypatch.setattr("playwright._impl._driver.get_driver_env", lambda: dict(os.environ))
    monkeypatch.setattr(
        "doc_dl.runtime.subprocess.run",
        lambda *args, **kwargs: FakeCompleted(),
    )
    monkeypatch.setattr("doc_dl.runtime.chromium_executable_path", lambda: None)

    with pytest.raises(DocDlError) as raised:
        install_chromium(state=state)
    assert raised.value.identifier == "browser_unavailable"
    assert "did not produce" in raised.value.message


def test_uninstall_chromium_refuses_bundled_full_build(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "doc-dl.exe"
    browser_root = tmp_path / "ms-playwright"
    browser_root.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    state = StatePaths(tmp_path / "state")

    with pytest.raises(DocDlError) as raised:
        uninstall_chromium(state)
    assert raised.value.identifier == "invalid_arguments"
    assert browser_root.exists()


def test_uninstall_chromium_removes_managed_state_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    state = StatePaths(tmp_path / "state")
    monkeypatch.setattr(
        "doc_dl.runtime.chromium_executable_path",
        lambda: None,
    )
    managed = state.browsers()
    managed.mkdir(parents=True)
    (managed / "marker.txt").write_text("data", encoding="utf-8")

    assert uninstall_chromium(state) is True
    assert not managed.exists()
    assert uninstall_chromium(state) is False


def test_effective_browsers_path_falls_back_to_state_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setattr("doc_dl.runtime.chromium_executable_path", lambda: None)
    state = StatePaths(tmp_path / "state")

    assert effective_browsers_path(state) == state.browsers()
