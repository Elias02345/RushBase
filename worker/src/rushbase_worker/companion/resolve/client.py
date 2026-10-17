"""Run the Resolve bridge with ResolvePython (21.1+) or a configured interpreter (ADR-0002)."""

import json
import os
import subprocess
import sys
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

from rushbase_worker.companion.clips import Clip
from rushbase_worker.config import WorkerSettings, default_data_dir

KEYS_FILE = "resolve_keys.json"
CLIP_COLORS = {"pending": "Orange", "done": "Green"}
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class ResolveError(Exception):
    """Resolve is missing, not reachable, or a call failed."""


def _resolve_python_default() -> Path:
    if sys.platform.startswith("win"):
        return Path(
            r"C:\Program Files\Blackmagic Design\DaVinci Resolve\ResolvePython\ResolvePython.exe"
        )
    if sys.platform == "darwin":
        return Path(
            "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/ResolvePython"
        )
    return Path("/opt/resolve/bin/ResolvePython")


def _script_defaults() -> tuple[Path, Path]:
    """Default ``RESOLVE_SCRIPT_API`` and ``RESOLVE_SCRIPT_LIB`` for the current OS."""
    if sys.platform.startswith("win"):
        api = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / (
            r"Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
        )
        return api, Path(r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll")
    if sys.platform == "darwin":
        api = Path(
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
        )
        lib = Path(
            "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
        )
        return api, lib
    return Path("/opt/resolve/Developer/Scripting"), Path(
        "/opt/resolve/libs/Fusion/fusionscript.so"
    )


def find_interpreter(settings: WorkerSettings) -> tuple[Path | None, bool]:
    """``(interpreter, is_resolve_python)``: the configured Python, else ResolvePython if present."""
    if settings.resolve_python:
        return settings.resolve_python, False
    candidate = _resolve_python_default()
    return (candidate, True) if candidate.exists() else (None, False)


def bridge_script() -> Path:
    """Path of the bundled bridge script."""
    return Path(str(resources.files("rushbase_worker.companion.resolve").joinpath("bridge.py")))


def bridge_env(settings: WorkerSettings, resolve_python: bool) -> dict[str, str]:
    """Environment for the bridge process (older Pythons need the scripting variables)."""
    env = dict(os.environ)
    if resolve_python:
        return env
    api, lib = _script_defaults()
    api = settings.resolve_script_api or api
    lib = settings.resolve_script_lib or lib
    env["RESOLVE_SCRIPT_API"] = str(api)
    env["RESOLVE_SCRIPT_LIB"] = str(lib)
    env["PYTHONPATH"] = os.pathsep.join([str(api / "Modules"), env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )
    return env


def run_bridge(
    settings: WorkerSettings,
    request: dict[str, Any],
    *,
    runner: Runner = subprocess.run,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Send ``request`` to the bridge and return its ``data``; raises :class:`ResolveError`."""
    interpreter, is_resolve_python = find_interpreter(settings)
    if interpreter is None:
        raise ResolveError(
            "no Resolve Python found: install DaVinci Resolve Studio 21.1+ or set "
            "RUSHBASE_RESOLVE_PYTHON to a Python 3 that can run Resolve scripts"
        )
    try:
        process = runner(
            [str(interpreter), str(bridge_script())],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=bridge_env(settings, is_resolve_python),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ResolveError(f"could not run the Resolve bridge: {exc}") from exc
    try:
        document = json.loads(process.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise ResolveError(f"bridge returned no result: {process.stderr.strip()[-300:]}") from exc
    if not document.get("ok"):
        raise ResolveError(str(document.get("error", "unknown bridge error")))
    return dict(document["data"])


def resolve_status(settings: WorkerSettings, *, runner: Runner = subprocess.run) -> dict[str, Any]:
    """Detect installation, edition, version and reachability (read-only)."""
    interpreter, is_resolve_python = find_interpreter(settings)
    status: dict[str, Any] = {
        "installed": interpreter is not None,
        "interpreter": str(interpreter) if interpreter else None,
        "resolve_python": is_resolve_python,
        "reachable": False,
        "studio": False,
        "product": None,
        "version": None,
        "project": None,
        "error": None,
    }
    if interpreter is None:
        status["error"] = "DaVinci Resolve Studio 21.1+ not found"
        return status
    try:
        status.update(run_bridge(settings, {"op": "probe"}, runner=runner, timeout=30.0))
        status["reachable"] = True
        if not status["studio"]:
            status["error"] = "DaVinci Resolve Free cannot be scripted; use the file exports"
    except ResolveError as exc:
        status["error"] = str(exc)
    return status


def clip_request(clip: Clip, path: Path, metadata_keys: tuple[str, ...]) -> dict[str, Any]:
    """One clip of a push request: path, mapped metadata, shots and colour."""
    comments = f"rushbase:{clip.asset_id}"
    values = {
        "Description": clip.description or clip.title or "",
        "Keywords": ", ".join(clip.keywords),
        "Comments": comments,
        "Shot": next((s.shot_size for s in clip.selected_shots() if s.shot_size), "") or "",
    }
    return {
        "asset_id": clip.asset_id,
        "path": str(path),
        "fps": clip.fps,
        "metadata": {k: v for k, v in values.items() if k in metadata_keys},
        "shots": [
            {"idx": s.idx, "start_ms": s.start_ms, "end_ms": s.end_ms, "description": s.description}
            for s in clip.shots
            if clip.selected is None or s.idx in clip.selected
        ],
        "clip_color": CLIP_COLORS.get(clip.review_state),
    }


def load_known_keys(data_dir: Path | None = None) -> list[str]:
    """Metadata key names discovered earlier (``GetMetadata`` probe cache)."""
    try:
        return list(json.loads(((data_dir or default_data_dir()) / KEYS_FILE).read_text("utf-8")))
    except (OSError, ValueError):
        return []


def save_known_keys(keys: list[str], data_dir: Path) -> None:
    """Cache discovered key names."""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / KEYS_FILE).write_text(json.dumps(sorted(set(keys))) + "\n", encoding="utf-8")


def push_clips(
    settings: WorkerSettings,
    clips: list[Clip],
    local_path: Callable[[Clip], Path | None],
    *,
    bin_name: str,
    markers: bool,
    metadata: bool,
    project: str | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Import ``clips`` into ``bin_name`` of the open Resolve project."""
    entries: list[dict[str, Any]] = []
    skipped: list[str] = []
    allowed = ("Description", "Keywords", "Comments", "Shot")
    for clip in clips:
        path = local_path(clip)
        if path is None:
            skipped.append(f"{clip.filename}: no local path")
            continue
        entries.append(clip_request(clip, path, allowed))
    known = load_known_keys(settings.data_dir)
    request = {
        "op": "push", "bin": bin_name, "clips": entries, "markers": markers, "metadata": metadata,
        "project": project, "known_keys": known,
    }  # fmt: skip
    result = run_bridge(settings, request, runner=runner)
    save_known_keys(result.get("keys", []), settings.data_dir)
    result["skipped"] = skipped
    return result


def read_usage(settings: WorkerSettings, *, runner: Runner = subprocess.run) -> dict[str, Any]:
    """Rushbase clips of the open project and how often each is used on a timeline (read-only)."""
    return run_bridge(settings, {"op": "usage"}, runner=runner)
