from __future__ import annotations

import importlib.metadata
import os
import platform
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from doc_dl.config import StatePaths
from doc_dl.runtime import build_variant, chromium_executable_path, effective_browsers_path


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

    checks.append(DoctorCheck("build", True, build_variant(), required=False))

    executable = chromium_executable_path()
    if executable is not None and executable.is_file():
        checks.append(DoctorCheck("chromium", True, str(executable), required=False))
    else:
        checks.append(
            DoctorCheck(
                "chromium",
                False,
                "not installed; run 'doc-dl install-browser', or it installs "
                "automatically the first time a browser-backed site needs it",
                required=False,
            )
        )
    checks.append(
        DoctorCheck("browser-storage", True, str(effective_browsers_path(paths)), required=False)
    )

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
