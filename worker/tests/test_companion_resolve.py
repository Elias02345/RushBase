"""The Resolve bridge, executed for real against a fake DaVinciResolveScript module."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from companion_data import ASSET_ID, asset_detail
from rushbase_worker.companion.clips import clip_from_asset
from rushbase_worker.companion.resolve import client
from rushbase_worker.companion.resolve.selftest import selftest
from rushbase_worker.config import WorkerSettings

FAKE = Path(__file__).parent / "fake_resolve"


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WorkerSettings:
    api = tmp_path / "scripting"
    (api / "Modules").mkdir(parents=True)
    (api / "Modules" / "DaVinciResolveScript.py").write_text(
        (FAKE / "DaVinciResolveScript.py").read_text()
    )
    monkeypatch.setenv("FAKE_RESOLVE_STATE", str(tmp_path / "state.json"))
    monkeypatch.delenv("FAKE_RESOLVE_MODE", raising=False)
    return WorkerSettings(
        resolve_python=Path(sys.executable), resolve_script_api=api, data_dir=tmp_path / "data"
    )


@pytest.fixture
def media(tmp_path: Path) -> Path:
    path = tmp_path / "A001_C002.mov"
    path.write_bytes(b"x")
    return path


def push(settings: WorkerSettings, media: Path, **options: Any) -> dict[str, Any]:
    clip = clip_from_asset(asset_detail())
    values: dict[str, Any] = {"bin_name": "Night", "markers": True, "metadata": True, **options}
    return client.push_clips(settings, [clip], lambda _c: media, **values)


def state(settings: WorkerSettings, tmp_path: Path) -> dict[str, Any]:
    return dict(json.loads((tmp_path / "state.json").read_text()))


def test_status_reports_studio(settings: WorkerSettings) -> None:
    status = client.resolve_status(settings)
    assert status["reachable"] and status["studio"]
    assert status["version"] == "21.1.1.0003" and status["project"] == "Untitled"


def test_status_free_edition_cannot_be_scripted(
    settings: WorkerSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RESOLVE_MODE", "free")
    status = client.resolve_status(settings)
    assert status["reachable"] and not status["studio"]
    assert "Free" in status["error"]


def test_status_when_resolve_is_not_running(
    settings: WorkerSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RESOLVE_MODE", "down")
    status = client.resolve_status(settings)
    assert not status["reachable"] and "not reachable" in status["error"]


def test_push_imports_sets_metadata_markers_and_color(
    settings: WorkerSettings, media: Path, tmp_path: Path
) -> None:
    result = push(settings, media)
    assert result["errors"] == []
    assert (
        result["imported"],
        result["metadata_set"],
        result["markers_added"],
        result["colored"],
    ) == (1, 1, 2, 1)
    item = state(settings, tmp_path)["items"][str(media)]
    assert item["metadata"]["Description"] == "Nachtaufnahme im Hafen"
    assert item["metadata"]["Keywords"] == "Hafen, Nacht"
    assert item["third"] == {"rushbase_asset_id": ASSET_ID}
    assert item["color"] == "Orange"
    markers = item["markers"]
    assert set(markers) == {"0", "100"}
    assert (
        markers["100"]["customData"] == f"rushbase:{ASSET_ID}:1"
        and markers["100"]["note"] == "Boot"
    )
    assert "Description" in client.load_known_keys(settings.data_dir)


def test_push_is_idempotent(settings: WorkerSettings, media: Path, tmp_path: Path) -> None:
    push(settings, media)
    again = push(settings, media)
    assert (again["imported"], again["existing"], again["markers_added"]) == (0, 1, 0)
    assert state(settings, tmp_path)["bins"]["Night"] == [str(media)]


def test_push_without_markers_and_metadata(
    settings: WorkerSettings, media: Path, tmp_path: Path
) -> None:
    result = push(settings, media, markers=False, metadata=False)
    assert result["markers_added"] == 0 and result["metadata_set"] == 0
    assert state(settings, tmp_path)["items"][str(media)]["markers"] == {}


def test_failed_api_calls_are_reported(
    settings: WorkerSettings, media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RESOLVE_MODE", "reject-metadata")
    result = push(settings, media)
    assert any("SetMetadata rejected" in e for e in result["errors"])


def test_import_failure_is_reported(settings: WorkerSettings, tmp_path: Path) -> None:
    result = push(settings, tmp_path / "missing.mov")
    assert result["imported"] == 0 and "ImportMedia failed" in result["errors"][0]


def test_wrong_project_aborts(settings: WorkerSettings, media: Path) -> None:
    with pytest.raises(client.ResolveError, match="expected 'Other'"):
        push(settings, media, project="Other")


def test_assets_without_local_path_are_skipped(settings: WorkerSettings) -> None:
    result = client.push_clips(settings, [clip_from_asset(asset_detail())], lambda _c: None,
                               bin_name="B", markers=False, metadata=False)  # fmt: skip
    assert result["skipped"] == ["A001_C002.mov: no local path"]


def test_missing_interpreter_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "find_interpreter", lambda _s: (None, False))
    with pytest.raises(client.ResolveError, match="no Resolve Python"):
        client.run_bridge(WorkerSettings(), {"op": "probe"})


def test_bridge_env_for_external_python(tmp_path: Path) -> None:
    env = client.bridge_env(WorkerSettings(resolve_script_api=tmp_path), resolve_python=False)
    assert env["RESOLVE_SCRIPT_API"] == str(tmp_path)
    assert env["PYTHONPATH"].startswith(str(tmp_path / "Modules"))
    assert "PYTHONPATH" not in client.bridge_env(WorkerSettings(), True)


def fake_ffmpeg(command: list[str], **kwargs: Any) -> "subprocess.CompletedProcess[str]":
    """Pretend to encode the test clip; everything else (the bridge) really runs."""
    if "lavfi" in command:
        Path(command[-1]).write_bytes(b"x")
        return subprocess.CompletedProcess(command, 0, "", "")
    return subprocess.run(command, **kwargs)


@pytest.fixture
def fake_toolbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "rushbase_worker.companion.resolve.selftest.Toolbox.ffmpeg_path", "ffmpeg", raising=False
    )


@pytest.mark.usefixtures("fake_toolbox")
def test_selftest_in_throwaway_project(settings: WorkerSettings, tmp_path: Path) -> None:
    result = selftest(settings, "rushbase-test", tmp_path / "work", runner=fake_ffmpeg)
    assert result["ok"] is True
    assert result["checks"] == {"imported": True, "metadata": True, "third_party": True,
                                "markers": True, "idempotent": True}  # fmt: skip
    data = state(settings, tmp_path)
    assert data["current"] == "Untitled"  # the previous project is restored
    assert "rushbase-test" in data["projects"]


@pytest.mark.usefixtures("fake_toolbox")
def test_selftest_refuses_other_project_names(settings: WorkerSettings, tmp_path: Path) -> None:
    with pytest.raises(client.ResolveError, match="rushbase-test"):
        selftest(settings, "my-real-project", tmp_path / "w", runner=fake_ffmpeg)
    assert not (tmp_path / "state.json").exists() or state(settings, tmp_path)["projects"] == [
        "Untitled"
    ]
