from __future__ import annotations

import json
from pathlib import Path

from doc_dl import __version__
from doc_dl.cli import parse_duration, run
from tests.fixture_server import FixtureServer


def test_duration_parser() -> None:
    assert parse_duration("90") == 90
    assert parse_duration("2m") == 120
    assert parse_duration("1.5h") == 5400


def test_bare_invocation_prints_help_and_examples(capsys) -> None:
    code = run([])

    assert code == 0
    out = capsys.readouterr().out
    assert "usage:" in out
    assert "Quick examples:" in out
    assert "doc-dl doctor" in out


def test_version_command(capsys) -> None:
    assert run(["version"]) == 0
    assert capsys.readouterr().out.startswith(f"doc-dl {__version__}")


def test_provider_listing_json(capsys) -> None:
    assert run(["providers", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {item["name"] for item in payload["providers"]} == {"generic", "scribd", "slideshare"}


def test_powershell_url_form_downloads_document(
    fixture_server: FixtureServer,
    tmp_path: Path,
    capsys,
) -> None:
    code = run(
        [
            "-Url",
            fixture_server.url("/files/sample.pdf"),
            "--no-browser",
            "--output",
            str(tmp_path),
        ]
    )
    assert code == 0
    output_lines = capsys.readouterr().out.strip().splitlines()
    assert Path(output_lines[-1]).is_file()


def test_json_error_has_stable_exit_code(capsys) -> None:
    code = run(["--json", "not-a-url"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "invalid_arguments"
    assert payload["exit_code"] == 2


def test_invalid_url_port_has_stable_error(capsys) -> None:
    code = run(["--json", "https://example.com:not-a-port/document.pdf"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "invalid_arguments"
    assert payload["message"] == "The supplied URL has an invalid port"


def test_unexpected_exception_is_reported_instead_of_crashing(monkeypatch, capsys) -> None:
    def _boom(argv: object) -> int:
        raise RuntimeError("Target page, context or browser has been closed")

    monkeypatch.setattr("doc_dl.cli._run_download", _boom)

    code = run(["--json", "https://example.com/document.pdf"])

    assert code == 99
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "internal_error"
    assert payload["exit_code"] == 99


def test_install_browser_reports_when_already_installed(monkeypatch, capsys) -> None:
    fake_executable = Path("/fake/chrome")
    monkeypatch.setattr("doc_dl.cli.is_chromium_installed", lambda: True)
    monkeypatch.setattr("doc_dl.cli.chromium_executable_path", lambda: fake_executable)

    code = run(["install-browser"])

    assert code == 0
    assert "already installed" in capsys.readouterr().out


def test_install_browser_installs_when_missing(monkeypatch, capsys) -> None:
    fake_executable = Path("/fake/chrome")
    monkeypatch.setattr("doc_dl.cli.is_chromium_installed", lambda: False)
    monkeypatch.setattr("doc_dl.cli.install_chromium", lambda sink: fake_executable)

    code = run(["install-browser"])

    assert code == 0
    assert str(fake_executable) in capsys.readouterr().out


def test_install_browser_force_reinstalls_even_if_present(monkeypatch, capsys) -> None:
    fake_executable = Path("/fake/chrome")
    monkeypatch.setattr("doc_dl.cli.is_chromium_installed", lambda: True)
    calls = []
    monkeypatch.setattr(
        "doc_dl.cli.install_chromium", lambda sink: calls.append(sink) or fake_executable
    )

    code = run(["install-browser", "--force"])

    assert code == 0
    assert len(calls) == 1
    assert str(fake_executable) in capsys.readouterr().out


def test_uninstall_browser_removes_with_yes_flag(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DOC_DL_STATE_DIR", str(tmp_path / "state"))
    target = tmp_path / "state" / "browsers"
    monkeypatch.setattr("doc_dl.cli.effective_browsers_path", lambda state: target)
    monkeypatch.setattr("doc_dl.cli.uninstall_chromium", lambda state: True)

    code = run(["uninstall-browser", "--yes"])

    assert code == 0
    assert "Removed" in capsys.readouterr().out


def test_clean_reports_no_files_when_directory_is_empty(tmp_path: Path, capsys) -> None:
    code = run(["clean", str(tmp_path), "--yes"])

    assert code == 0
    assert "No leftover" in capsys.readouterr().out


def test_clean_removes_orphaned_partial_files_with_yes_flag(tmp_path: Path, capsys) -> None:
    part = tmp_path / ".doc-dl-abc123.part"
    sidecar = tmp_path / ".doc-dl-abc123.part.json"
    part.write_bytes(b"partial")
    sidecar.write_text("{}", encoding="utf-8")

    code = run(["clean", str(tmp_path), "--yes"])

    assert code == 0
    assert not part.exists()
    assert not sidecar.exists()
    assert "Removed 2" in capsys.readouterr().out


def test_clean_prompts_and_respects_decline(tmp_path: Path, monkeypatch, capsys) -> None:
    part = tmp_path / ".doc-dl-abc123.part"
    part.write_bytes(b"partial")
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    code = run(["clean", str(tmp_path)])

    assert code == 0
    assert part.exists()
    assert "No files were deleted." in capsys.readouterr().out


def test_uninstall_browser_reports_nothing_to_remove(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DOC_DL_STATE_DIR", str(tmp_path / "state"))
    target = tmp_path / "state" / "browsers"
    monkeypatch.setattr("doc_dl.cli.effective_browsers_path", lambda state: target)
    monkeypatch.setattr("doc_dl.cli.uninstall_chromium", lambda state: False)

    code = run(["uninstall-browser", "--yes"])

    assert code == 0
    assert "No installed browser runtime was found." in capsys.readouterr().out
