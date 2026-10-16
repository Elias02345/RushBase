"""Companion CLI commands: every command prints one JSON envelope with the documented shape."""

import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from companion_data import ASSET_ID, OTHER_ID, last_json, server_handler
from rushbase_worker import cli

runner = CliRunner()


@pytest.fixture(autouse=True)
def companion_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    home = tmp_path_factory.mktemp("companion-home")
    root = home / "lib"
    root.mkdir()
    monkeypatch.setenv("RUSHBASE_DATA_DIR", str(home / "data"))
    monkeypatch.setenv("RUSHBASE_WORKER_CONFIG", str(home / "none.toml"))
    monkeypatch.setenv("RUSHBASE_SERVER_URL", "http://srv")
    monkeypatch.setenv("RUSHBASE_API_TOKEN", "rbp_" + "t" * 30)
    monkeypatch.setenv("RUSHBASE_LIBRARY_ROOT", str(root))
    monkeypatch.delenv("RUSHBASE_LIBRARY_ID", raising=False)
    monkeypatch.delenv("RUSHBASE_API_KEY_FILE", raising=False)
    monkeypatch.chdir(home)
    real = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: real(**{**kw, "transport": httpx.MockTransport(server_handler)}),
    )
    return root


def run(*args: str, ok: bool = True, stdin: str | None = None) -> dict[str, Any]:
    result = runner.invoke(cli.app, [*args, "--json"], input=stdin)
    document = last_json(result.output)
    assert result.exit_code == (0 if ok else 1), result.output
    assert document["schema_version"] == 1
    assert document["ok"] is ok
    assert set(document) == {"schema_version", "command", "ok", "data"}
    return document


def test_login_stores_a_personal_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUSHBASE_API_TOKEN")
    document = run(
        "login", "--server", "http://srv", "--username", "elias", "--password-stdin", stdin="pw\n"
    )
    assert document["command"] == "login"
    token_file = Path(document["data"]["token_file"])
    assert token_file.read_text().strip().startswith("rbp_")
    assert oct(token_file.stat().st_mode & 0o777) == "0o600"
    assert run("search", "harbour")["ok"] is True


def test_search_json_and_filters() -> None:
    document = run(
        "search",
        "harbour",
        "--filter",
        "media_type=video",
        "--filter",
        "tag=location:harbour",
        "--level",
        "shot",
    )
    data = document["data"]
    assert document["command"] == "search"
    assert data["items"][0]["asset"]["id"] == ASSET_ID
    assert {"items", "level", "offset", "next_offset", "candidates"} <= set(data)


def test_search_rejects_bad_filter() -> None:
    document = run("search", "x", "--filter", "nonsense", ok=False)
    assert "invalid filter" in document["data"]["error"]


def test_search_without_token_explains_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUSHBASE_API_TOKEN")
    document = run("search", "x", ok=False)
    assert "rushbase login" in document["data"]["error"]


def test_browse_and_filters() -> None:
    page = run("browse", "--filter", "media_type=video", "--sort", "-date")["data"]
    assert page["items"][0]["id"] == ASSET_ID and page["next_cursor"] is None
    assert run("filters")["data"]["facets"][0]["key"] == "media_type"


def test_asset_get_includes_local_path(companion_env: Path) -> None:
    data = run("asset", "get", ASSET_ID)["data"]
    assert data["filename"] == "A001_C002.mov"
    assert data["local_path"] == str(companion_env / "Content" / "2026" / "A001_C002.mov")
    assert data["local_exists"] is False


def test_asset_get_unknown_is_an_error() -> None:
    assert "404" in run("asset", "get", OTHER_ID, ok=False)["data"]["error"]


def test_selection_lifecycle() -> None:
    assert run("select", "list")["data"] == {"count": 0, "items": []}
    added = run("select", "add", ASSET_ID, "--shots", "0")["data"]
    assert added["items"][0]["selected_shots"] == [0]
    merged = run("select", "add", ASSET_ID, "--shots", "1")["data"]
    assert merged["items"][0]["selected_shots"] == [0, 1]
    trimmed = run("select", "remove", ASSET_ID, "--shots", "0")["data"]
    assert trimmed["items"][0]["selected_shots"] == [1]
    assert run("select", "remove", ASSET_ID)["data"]["count"] == 0
    assert "not in the selection" in run("select", "remove", ASSET_ID, ok=False)["data"]["error"]
    run("select", "add", ASSET_ID)
    assert run("select", "clear")["data"]["count"] == 0


def test_select_add_unknown_shot() -> None:
    assert "no shot" in run("select", "add", ASSET_ID, "--shots", "9", ok=False)["data"]["error"]


def test_paths_resolve(companion_env: Path) -> None:
    data = run("paths", "resolve", ASSET_ID)["data"]
    assert data["library_root"] == str(companion_env)
    file = data["files"][0]
    assert file["paths"]["linux"].startswith("/mnt/lib/")
    assert file["local_path"].startswith(str(companion_env))
    assert file["exists"] is False


@pytest.mark.parametrize("kind", ["fcpxml", "edl", "csv"])
def test_export_commands(kind: str, tmp_path: Path) -> None:
    run("select", "add", ASSET_ID)
    out = tmp_path / f"cut.{kind}"
    data = run("export", kind, "--out", str(out))["data"]
    assert (
        data["format"] == kind and data["file"] == str(out) and data["bytes"] == out.stat().st_size
    )
    assert isinstance(data["warnings"], list)
    assert "A001_C002" in out.read_text(encoding="utf-8-sig")


def test_export_csv_columns(tmp_path: Path) -> None:
    run("select", "add", ASSET_ID)
    out = tmp_path / "c.csv"
    run("export", "csv", "--out", str(out), "--columns", "File Name,Comments")
    assert out.read_bytes().decode("utf-8-sig").startswith("File Name,Comments\r\n")


def test_export_empty_selection_fails(tmp_path: Path) -> None:
    assert (
        "selection is empty"
        in run("export", "edl", "--out", str(tmp_path / "x.edl"), ok=False)["data"]["error"]
    )


def test_resolve_send_requires_confirmation() -> None:
    result = runner.invoke(cli.app, ["resolve", "send", "--json"])
    assert result.exit_code == 2
    assert "--yes" in last_json(result.output)["data"]["error"]


def test_resolve_selftest_requires_confirmation() -> None:
    result = runner.invoke(cli.app, ["resolve", "selftest", "--json"])
    assert result.exit_code == 2


def test_resolve_status_without_installation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "rushbase_worker.companion.resolve.client.find_interpreter", lambda _s: (None, False)
    )
    data = run("resolve", "status", ok=False)["data"]
    assert data["installed"] is False and data["reachable"] is False
    assert {"studio", "version", "project", "error", "interpreter"} <= set(data)


def test_overview_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    data = run("overview", "--no-mount-check")["data"]
    assert data["server"]["reachable"] is True
    assert data["totals"] == {"files": 40, "hours": 3.5, "jobs": 70}
    assert set(data["hardware"]) == {
        "gpu",
        "vram_total_mb",
        "vram_free_mb",
        "backend",
        "profile",
        "cpu",
        "cores",
    }
    assert set(data["models"]) == {"needed", "installed", "missing"}
    assert data["estimate"]["total_seconds"] > 0
    assert set(data["estimate"]["stages"]["vision"]) == {
        "pending_jobs",
        "mean_job_ms",
        "seconds",
        "source",
    }
    assert isinstance(data["issues"], list) and isinstance(data["ready"], bool)


def test_overview_guides_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUSHBASE_SERVER_URL")
    data = run("overview", "--no-mount-check")["data"]
    assert [i["code"] for i in data["issues"] if i["code"] != "models_missing"] == ["no_server"]
    assert data["ready"] is False


def test_overview_reports_unreachable_server(monkeypatch: pytest.MonkeyPatch) -> None:
    from rushbase_worker.companion import service
    from rushbase_worker.companion.api import UserApiError

    def refuse(_settings: object) -> dict[str, Any]:
        raise UserApiError(0, "server unreachable: refused")

    monkeypatch.setattr(service, "fetch_backlog", refuse)
    data = run("overview", "--no-mount-check")["data"]
    assert "server_unreachable" in [i["code"] for i in data["issues"]]
    assert data["server"]["reachable"] is False


def test_process_records_throughput(monkeypatch: pytest.MonkeyPatch) -> None:
    from rushbase_worker.companion import throughput
    from rushbase_worker.config import WorkerSettings

    throughput.record_jobs(WorkerSettings(), {"preview": [1000, 3000, 2000]})
    history = throughput.load_history(WorkerSettings())
    assert throughput.mean_job_ms(history, "preview") == (2000, "history")
    assert throughput.mean_job_ms(history, "vision")[1] == "default"
    estimate = throughput.estimate({"stages": {"preview": {"pending_jobs": 8}}}, history, 4)
    assert estimate["stages"]["preview"]["seconds"] == 4
    assert re.fullmatch(r"\d+", str(estimate["total_seconds"]))
