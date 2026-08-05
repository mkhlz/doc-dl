from __future__ import annotations

import os
import sys
from pathlib import Path

from doc_dl.runtime import configure_bundled_runtime


def test_standard_install_does_not_change_playwright_path(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    assert configure_bundled_runtime() is None
    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ


def test_frozen_install_uses_browser_beside_executable(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "doc-dl.exe"
    browser_root = tmp_path / "ms-playwright"
    browser_root.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    assert configure_bundled_runtime() == browser_root
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(browser_root)


def test_frozen_install_preserves_explicit_browser_path(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "doc-dl"
    browser_root = tmp_path / "ms-playwright"
    browser_root.mkdir()
    explicit = tmp_path / "custom-browsers"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(explicit))

    assert configure_bundled_runtime() == browser_root
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(explicit)
