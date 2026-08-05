from __future__ import annotations

import importlib.metadata
import os
import platform
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from doc_dl.config import StatePaths


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str
    required: bool = True


def run_doctor(state: StatePaths | None = None) -> list[DoctorCheck]:
    paths = state or StatePaths.discover()
    checks = [
        DoctorCheck(
            "python",
            sys.version_info >= (3, 11),
            f"{platform.python_implementation()} {platform.python_version()}",
        ),
    ]
    for distribution in ("httpx", "pillow", "playwright", "pypdf"):
        try:
            version = importlib.metadata.version(distribution)
            checks.append(DoctorCheck(distribution, True, version))
        except importlib.metadata.PackageNotFoundError:
            checks.append(DoctorCheck(distribution, False, "not installed"))

    browser_ok = False
    browser_detail = "Playwright is not installed"
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
            if executable.is_file():
                browser = playwright.chromium.launch(channel="chromium", headless=True)
                browser.close()
                browser_ok = True
                browser_detail = str(executable)
            else:
                browser_detail = f"missing: {executable}"
    except Exception as exc:
        browser_detail = str(exc)
    checks.append(DoctorCheck("chromium", browser_ok, browser_detail))

    try:
        paths.root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="doc-dl-doctor-",
            dir=paths.root,
            delete=False,
        ) as handle:
            probe = Path(handle.name)
            handle.write(b"ok")
            handle.flush()
            os.fsync(handle.fileno())
        probe.unlink()
        checks.append(DoctorCheck("state-directory", True, str(paths.root)))
    except OSError as exc:
        checks.append(DoctorCheck("state-directory", False, str(exc)))

    return checks


def doctor_payload(checks: list[DoctorCheck]) -> dict[str, object]:
    return {
        "event": "doctor",
        "version": 1,
        "ok": all(check.ok or not check.required for check in checks),
        "checks": [asdict(check) for check in checks],
    }
