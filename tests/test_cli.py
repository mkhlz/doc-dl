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


def test_version_command(capsys) -> None:
    assert run(["version"]) == 0
    assert capsys.readouterr().out.startswith(f"doc-dl {__version__}")


def test_provider_listing_json(capsys) -> None:
    assert run(["providers", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {item["name"] for item in payload["providers"]} == {"generic", "scribd"}


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
