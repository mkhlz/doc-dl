from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from doc_dl.config import StatePaths
from doc_dl.errors import DocDlError

if TYPE_CHECKING:
    from doc_dl.events import EventSink

_CHROMIUM_DIR = re.compile(r"^chromium-\d+$")


def bundled_offline_browsers_dir() -> Path | None:
    """Chromium bundled beside a frozen 'full' executable, if this build shipped one."""
    if not getattr(sys, "frozen", False):
        return None
    browser_root = Path(sys.executable).resolve().parent / "ms-playwright"
    return browser_root if browser_root.is_dir() else None


def configure_browsers_path(state: StatePaths | None = None) -> Path | None:
    """Point Playwright at a stable Chromium directory for a frozen build.

    Precedence: an explicit ``PLAYWRIGHT_BROWSERS_PATH`` already set by the user
    or environment; Chromium bundled beside a frozen 'full' executable; doc-dl's
    per-user state directory, used by default for frozen 'slim' installs.

    Source and pip installs are left untouched so they keep using Playwright's
    own default per-user cache location, matching normal Playwright behavior
    for developers and other tools sharing that cache.
    """
    if not getattr(sys, "frozen", False):
        return None

    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        return Path(override).expanduser()

    bundled = bundled_offline_browsers_dir()
    if bundled is not None:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)
        return bundled

    browsers_dir = (state or StatePaths.discover()).browsers()
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir)
    return browsers_dir


def build_variant() -> str:
    """Report whether this is a 'full' (Chromium bundled), 'slim', or 'source' build."""
    if not getattr(sys, "frozen", False):
        return "source"
    return "full" if bundled_offline_browsers_dir() is not None else "slim"


def effective_browsers_path(state: StatePaths | None = None) -> Path:
    """Best-effort resolution of where Playwright looks for or installs Chromium."""
    configured = configure_browsers_path(state)
    if configured is not None:
        return configured

    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        return Path(override).expanduser()

    executable = chromium_executable_path()
    if executable is not None:
        for candidate in executable.parents:
            if _CHROMIUM_DIR.match(candidate.name):
                return candidate.parent

    return (state or StatePaths.discover()).browsers()


def chromium_executable_path() -> Path | None:
    """The Chromium executable Playwright would launch, or None if unavailable."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path)
    except Exception:
        return None


def is_chromium_installed() -> bool:
    executable = chromium_executable_path()
    return executable is not None and executable.is_file()


def install_chromium(sink: EventSink | None = None, *, state: StatePaths | None = None) -> Path:
    """Download and install Chromium using Playwright's bundled Node driver.

    This does not require a system Python or Node installation: PyInstaller
    builds bundle the driver (including its Node runtime) via
    ``--collect-all playwright``, so this works standalone from a frozen exe.
    """
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
    except ImportError as exc:
        raise DocDlError(
            "browser_unavailable",
            "Playwright is not installed",
            detail="Reinstall doc-dl, or run 'pip install playwright' in your environment.",
        ) from exc

    browsers_path = configure_browsers_path(state) or effective_browsers_path(state)
    browsers_path.mkdir(parents=True, exist_ok=True)

    show_native_progress = sink is None or (not sink.json_mode and not sink.quiet)
    if sink is not None:
        sink.emit(
            "strategy",
            message=(
                "Chromium is not installed. Downloading the browser runtime now "
                f"(one-time download into {browsers_path})..."
            ),
            strategy="browser-install",
            status="started",
        )

    driver_executable, driver_cli = compute_driver_executable()
    env = get_driver_env()
    command = [driver_executable, driver_cli, "install", "chromium"]
    try:
        if show_native_progress:
            completed = subprocess.run(command, env=env, check=False)
        else:
            completed = subprocess.run(
                command,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    except OSError as exc:
        raise DocDlError(
            "browser_unavailable",
            "Could not start the Chromium installer",
            detail=str(exc),
        ) from exc

    if completed.returncode != 0:
        raise DocDlError(
            "browser_unavailable",
            "Chromium installation failed",
            detail=(
                f"'playwright install chromium' exited with code {completed.returncode}. "
                "Check your network connection and try 'doc-dl install-browser' again."
            ),
        )

    executable = chromium_executable_path()
    if executable is None or not executable.is_file():
        raise DocDlError(
            "browser_unavailable",
            "Chromium installation did not produce a usable browser",
            detail=f"Expected an executable under {browsers_path}",
        )

    if sink is not None:
        sink.emit(
            "strategy",
            message=f"Chromium installed at {executable}",
            strategy="browser-install",
            status="succeeded",
        )
    return executable


def uninstall_chromium(state: StatePaths | None = None) -> bool:
    """Remove the Chromium runtime doc-dl downloaded. Returns False if none existed.

    Refuses to touch Chromium bundled beside a frozen 'full' executable: that
    copy belongs to the installed program, not to on-demand state, and removing
    it would defeat the point of an offline build.
    """
    paths = state or StatePaths.discover()
    target = effective_browsers_path(paths)
    bundled = bundled_offline_browsers_dir()
    if bundled is not None and target.resolve() == bundled.resolve():
        raise DocDlError(
            "invalid_arguments",
            "Refusing to remove the Chromium runtime bundled with this offline build",
            detail=(
                "This is a 'full' portable build with Chromium included beside the "
                "executable. Reinstall doc-dl with the 'slim' build if you want Chromium "
                "to live in the per-user state directory instead."
            ),
        )
    if not target.exists():
        return False
    shutil.rmtree(target)
    return True
