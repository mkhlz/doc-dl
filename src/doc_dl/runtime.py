from __future__ import annotations

import os
import sys
from pathlib import Path


def configure_bundled_runtime() -> Path | None:
    """Point Playwright at Chromium shipped beside a frozen executable."""
    if not getattr(sys, "frozen", False):
        return None

    browser_root = Path(sys.executable).resolve().parent / "ms-playwright"
    if not browser_root.is_dir():
        return None

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browser_root))
    return browser_root
