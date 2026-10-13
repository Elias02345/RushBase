import json
import shutil
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from rushbase_core import init_library
from rushbase_worker import cli, doctor, get_version

runner = CliRunner()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("LIBRARY_ROOT", "LIBRARY_ID", "SERVER_URL"):
        monkeypatch.delenv(f"RUSHBASE_{key}", raising=False)


@pytest.fixture
def fake_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        doctor, "check_binary", lambda name, _args: doctor.Check(name, "ok", f"{name} 1.0")
    )


def test_version_plain() -> None:
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"rushbase {get_version()}"


def test_version_json_schema() -> None:
    result = runner.invoke(cli.app, ["version", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "schema_version": 1,
        "command": "version",
        "ok": True,
        "data": {"version": get_version()},
    }


def test_no_args_shows_help() -> None:
    result = runner.invoke(cli.app, [])
    assert "doctor" in result.output


@pytest.mark.usefixtures("fake_binaries")
def test_doctor_all_ok_with_library_and_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = init_library(tmp_path)
    monkeypatch.setattr(
        httpx, "get", lambda url, timeout: httpx.Response(200, request=httpx.Request("GET", url))
    )
    result = runner.invoke(
        cli.app,
        [
            "doctor",
            "--json",
            "--library",
            str(tmp_path),
            "--library-id",
            str(manifest.library_id),
            "--server-url",
            "http://srv:8080/",
        ],
    )
    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["ok"] is True
    assert [c["name"] for c in document["data"]["checks"]] == [
        "ffmpeg",
        "ffprobe",
        "exiftool",
        "library",
        "server",
    ]
    assert {c["status"] for c in document["data"]["checks"]} == {"ok"}


@pytest.mark.usefixtures("fake_binaries")
def test_doctor_skips_unconfigured_checks() -> None:
    result = runner.invoke(cli.app, ["doctor", "--json"])
    assert result.exit_code == 0
    statuses = {c["name"]: c["status"] for c in json.loads(result.output)["data"]["checks"]}
    assert statuses["library"] == "skipped"
    assert statuses["server"] == "skipped"


@pytest.mark.usefixtures("fake_binaries")
def test_doctor_fails_on_missing_library(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["doctor", "--json", "--library", str(tmp_path)])
    assert result.exit_code == 1
    document = json.loads(result.output)
    assert document["ok"] is False
    library = next(c for c in document["data"]["checks"] if c["name"] == "library")
    assert library["status"] == "error"


@pytest.mark.usefixtures("fake_binaries")
def test_doctor_fails_on_unreachable_server(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(url: str, timeout: float) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", refuse)
    result = runner.invoke(cli.app, ["doctor", "--server-url", "http://srv"])
    assert result.exit_code == 1
    assert "[ERROR  ] server" in result.output


def test_doctor_reads_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_library(tmp_path)
    monkeypatch.setenv("RUSHBASE_LIBRARY_ROOT", str(tmp_path))
    result = runner.invoke(cli.app, ["doctor", "--json"])
    checks = {c["name"]: c["status"] for c in json.loads(result.output)["data"]["checks"]}
    assert checks["library"] == "ok"


def test_check_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    check = doctor.check_binary("ffmpeg", ("-version",))
    assert check.status == "error"
    assert "not found" in check.detail


def test_check_binary_runs_real_executable() -> None:
    check = doctor.check_binary("python3", ("--version",))
    assert check.status == "ok"
    assert check.detail.startswith("Python")


def test_check_server_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda url, timeout: httpx.Response(503, request=httpx.Request("GET", url))
    )
    check = doctor.check_server("http://srv")
    assert check.status == "error"
    assert "503" in check.detail
