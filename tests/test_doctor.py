from __future__ import annotations

from pathlib import Path

from doc_dl.config import StatePaths
from doc_dl.doctor import doctor_payload, run_doctor


def test_missing_chromium_is_reported_but_not_required(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("doc_dl.doctor.chromium_executable_path", lambda: None)
    state = StatePaths(tmp_path / "state")

    checks = run_doctor(state)
    payload = doctor_payload(checks)

    chromium_check = next(check for check in checks if check.name == "chromium")
    assert chromium_check.ok is False
    assert chromium_check.required is False
    assert "install-browser" in chromium_check.detail
    assert payload["ok"] is True


def test_installed_chromium_is_reported_ok(tmp_path: Path, monkeypatch) -> None:
    fake_executable = tmp_path / "chrome.exe"
    fake_executable.write_bytes(b"binary")
    monkeypatch.setattr("doc_dl.doctor.chromium_executable_path", lambda: fake_executable)
    state = StatePaths(tmp_path / "state")

    checks = run_doctor(state)

    chromium_check = next(check for check in checks if check.name == "chromium")
    assert chromium_check.ok is True
    assert chromium_check.detail == str(fake_executable)


def test_browser_storage_and_build_checks_are_informational(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("doc_dl.doctor.chromium_executable_path", lambda: None)
    state = StatePaths(tmp_path / "state")

    checks = run_doctor(state)

    storage_check = next(check for check in checks if check.name == "browser-storage")
    build_check = next(check for check in checks if check.name == "build")
    assert storage_check.ok is True
    assert storage_check.required is False
    assert build_check.ok is True
    assert build_check.required is False
