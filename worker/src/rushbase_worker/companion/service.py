"""Library functions behind the CLI commands and the local MCP server (one implementation)."""

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx

from rushbase_worker.assets import ModelStore
from rushbase_worker.assets.plan import models_for_profile
from rushbase_worker.client import ApiError, AuthError, UnavailableError, WorkerClient
from rushbase_worker.companion import throughput
from rushbase_worker.companion.api import (
    UserApi,
    UserApiError,
    fetch_backlog,
    load_user_token,
    resolve_server_url,
)
from rushbase_worker.companion.clips import Clip, clip_from_asset, local_path
from rushbase_worker.companion.export_csv import DEFAULT_COLUMNS, export_csv
from rushbase_worker.companion.export_edl import export_edl
from rushbase_worker.companion.export_fcpxml import export_fcpxml
from rushbase_worker.companion.selection import (
    Basket,
    add_clip,
    load_basket,
    remove_clip,
    save_basket,
)
from rushbase_worker.config import WorkerSettings
from rushbase_worker.hardware import detect_hardware
from rushbase_worker.mount import MountError, verify_mount
from rushbase_worker.runtimes.manager import Runtimes
from rushbase_worker.stages import HANDLERS, runnable_stages
from rushbase_worker.state import load_api_key, load_identity, load_last_run
from rushbase_worker.tools.toolbox import Toolbox

HOUR_MS = 3_600_000


class CompanionError(Exception):
    """A user-facing failure (bad input, missing configuration, server error)."""


def parse_filters(pairs: list[str]) -> list[tuple[str, str]]:
    """Turn ``key=value`` strings into query parameters (repeat a key for alternatives)."""
    parsed: list[tuple[str, str]] = []
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip() or not value.strip():
            raise CompanionError(f"invalid filter {pair!r}: expected key=value")
        parsed.append((key.strip(), value.strip()))
    return parsed


def _api(settings: WorkerSettings) -> UserApi:
    try:
        return UserApi.from_settings(settings)
    except UserApiError as exc:
        raise CompanionError(str(exc)) from exc


def search(
    settings: WorkerSettings,
    query: str,
    filters: list[str] | None = None,
    level: str = "asset",
    limit: int = 30,
    offset: int = 0,
) -> dict[str, Any]:
    """Hybrid search on the server (`level` is ``asset`` or ``shot``)."""
    if level not in {"asset", "shot"}:
        raise CompanionError("level must be 'asset' or 'shot'")
    try:
        with _api(settings) as api:
            return api.search(
                query, parse_filters(filters or []), level=level, limit=limit, offset=offset
            )
    except UserApiError as exc:
        raise CompanionError(str(exc)) from exc


def browse(
    settings: WorkerSettings,
    filters: list[str] | None = None,
    sort: str | None = None,
    cursor: str | None = None,
    limit: int = 60,
) -> dict[str, Any]:
    """List assets (no query) with filters, sorting and keyset pagination."""
    params = [*parse_filters(filters or []), ("limit", str(limit))]
    if sort:
        params.append(("sort", sort))
    if cursor:
        params.append(("cursor", cursor))
    try:
        with _api(settings) as api:
            return dict(api.get("/api/v1/assets", params))
    except UserApiError as exc:
        raise CompanionError(str(exc)) from exc


def list_filters(settings: WorkerSettings) -> dict[str, Any]:
    """Filter dimensions (facets) with their values and counts."""
    try:
        with _api(settings) as api:
            return api.facets()
    except UserApiError as exc:
        raise CompanionError(str(exc)) from exc


def get_asset(settings: WorkerSettings, asset_id: str) -> dict[str, Any]:
    """Asset detail plus the path of its primary file on this machine."""
    try:
        with _api(settings) as api:
            detail = api.asset(asset_id)
    except UserApiError as exc:
        raise CompanionError(str(exc)) from exc
    clip = clip_from_asset(detail)
    path = local_path(settings, clip)
    detail["local_path"] = str(path) if path else None
    detail["local_exists"] = bool(path and path.exists())
    return detail


def keyframe_files(
    settings: WorkerSettings, asset_id: str, shot_idx: int | None = None, limit: int = 12
) -> list[dict[str, Any]]:
    """Keyframes of an asset (optionally one shot): metadata, local path or downloaded bytes."""
    detail = get_asset(settings, asset_id)
    root = settings.resolved_library_root
    frames: list[dict[str, Any]] = []
    for shot in detail.get("shots", []):
        if shot_idx is not None and shot["idx"] != shot_idx:
            continue
        for frame in shot.get("keyframes", []):
            if frame.get("url"):
                frames.append({"shot_idx": shot["idx"], "t_ms": frame["t_ms"],
                               "position": frame["position"], "url": frame["url"]})  # fmt: skip
    frames = frames[:limit]
    for frame in frames:
        sub = unquote(frame["url"].split(f"/{asset_id}/", 1)[-1])
        candidate = root / ".rushbase" / "derived" / asset_id / sub if root else None
        frame["path"] = str(candidate) if candidate and candidate.is_file() else None
    return frames


def read_keyframe(settings: WorkerSettings, frame: dict[str, Any]) -> bytes:
    """Bytes of a keyframe from the local mount, else downloaded from the server."""
    if frame.get("path"):
        return Path(frame["path"]).read_bytes()
    try:
        with _api(settings) as api:
            return api.get_bytes(frame["url"])
    except UserApiError as exc:
        raise CompanionError(str(exc)) from exc


def _shot_indexes(spec: list[int] | None) -> list[int] | None:
    return sorted(set(spec)) if spec else None


def select_add(settings: WorkerSettings, asset_id: str, shots: list[int] | None = None) -> Basket:
    """Add an asset (or some of its shots) to the basket; details are cached for offline use."""
    try:
        with _api(settings) as api:
            detail = api.asset(asset_id)
    except UserApiError as exc:
        raise CompanionError(str(exc)) from exc
    clip = clip_from_asset(detail, _shot_indexes(shots))
    if shots:
        known = {s.idx for s in clip.shots}
        unknown = sorted(set(shots) - known)
        if unknown:
            raise CompanionError(f"asset {asset_id} has no shot(s) {unknown}")
    basket = add_clip(load_basket(settings), clip)
    save_basket(settings, basket)
    return basket


def select_remove(
    settings: WorkerSettings, asset_id: str, shots: list[int] | None = None
) -> Basket:
    """Remove an asset (or only some selected shots) from the basket."""
    basket = load_basket(settings)
    if not remove_clip(basket, asset_id, shots):
        raise CompanionError(f"asset {asset_id} is not in the selection")
    save_basket(settings, basket)
    return basket


def select_clear(settings: WorkerSettings) -> Basket:
    """Empty the basket."""
    basket = Basket()
    save_basket(settings, basket)
    return basket


def resolve_paths(settings: WorkerSettings, asset_id: str) -> dict[str, Any]:
    """All paths of an asset: library-relative, per OS on the server, and on this machine."""
    detail = get_asset(settings, asset_id)
    files = [
        {"role": f["role"], "rel_path": f["rel_path"], "paths": f.get("paths", {}),
         "local_path": str(p) if (p := local_path(settings, Clip(asset_id=asset_id, rel_path=f["rel_path"], filename=""))) else None,
         "exists": bool(p and p.exists())}
        for f in detail.get("files", [])
    ]  # fmt: skip
    return {"asset_id": asset_id, "library_root": str(settings.resolved_library_root or ""),
            "files": files}  # fmt: skip


def basket_clips(settings: WorkerSettings, asset_ids: list[str] | None = None) -> list[Clip]:
    """Clips of the basket (optionally only some assets); error when empty."""
    clips = load_basket(settings).items
    if asset_ids:
        clips = [c for c in clips if c.asset_id in asset_ids]
    if not clips:
        raise CompanionError("the selection is empty: add assets with `rushbase select add`")
    return clips


def export_text(
    settings: WorkerSettings, kind: str, columns: tuple[str, ...] | None = None,
    asset_ids: list[str] | None = None, title: str = "Rushbase selection",
) -> tuple[str, list[str]]:  # fmt: skip
    """Render the basket as ``csv``, ``fcpxml`` or ``edl``; returns ``(text, warnings)``."""
    clips = basket_clips(settings, asset_ids)

    def path_of(clip: Clip) -> Path | None:
        return local_path(settings, clip)

    if kind == "csv":
        return export_csv(clips, path_of, columns or DEFAULT_COLUMNS), []
    if kind == "fcpxml":
        return export_fcpxml(clips, path_of, title)
    if kind == "edl":
        return export_edl(clips, title)
    raise CompanionError(f"unknown export format {kind!r} (csv, fcpxml, edl)")


def write_export(path: Path, text: str, kind: str) -> None:
    """Write an export file (UTF-8; CSV with BOM so spreadsheet tools detect the encoding)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig" if kind == "csv" else "utf-8", newline="")


def _mount_state(settings: WorkerSettings) -> dict[str, Any]:
    root = settings.resolved_library_root
    state: dict[str, Any] = {"root": str(root) if root else None, "ok": False, "error": None}
    key, url = load_api_key(settings), resolve_server_url(settings)
    if root is None:
        state["error"] = "no library root configured"
        return state
    if key is None or url is None:
        state["error"] = "cannot verify without a server"
        return state

    async def check() -> None:
        async with WorkerClient(url, key, retries=0, timeout=8.0) as client:
            info = await client.library()
            verify_mount(root, info.library_id, settings.library_id)

    try:
        asyncio.run(check())
        state["ok"] = True
    except MountError as exc:
        state["error"] = str(exc)
    except (AuthError, ApiError, UnavailableError, httpx.HTTPError) as exc:
        state["error"] = f"server: {exc}"
    return state


def _totals(backlog: dict[str, Any]) -> dict[str, Any]:
    """Unique backlog is approximated by the largest per-stage figure (a file appears in several)."""
    stages = backlog.get("stages", {}).values()
    files = max((s.get("pending_files", 0) for s in stages), default=0)
    ms = max((s.get("pending_duration_ms", 0) for s in stages), default=0)
    jobs = sum(s.get("pending_jobs", 0) for s in stages)
    return {"files": files, "hours": round(ms / HOUR_MS, 2), "jobs": jobs}


def overview(settings: WorkerSettings, *, probe_mount: bool = True) -> dict[str, Any]:
    """Everything the Processor home screen shows, in one document."""
    identity, key = load_identity(settings), load_api_key(settings)
    url = resolve_server_url(settings)
    issues: list[dict[str, str]] = []
    report = detect_hardware()
    gpu = max(report.gpus, key=lambda g: g.vram_total_mb or 0, default=None)
    toolbox = Toolbox(settings)
    runnable = runnable_stages(tuple(HANDLERS), toolbox, Runtimes(settings, lambda _e: None))
    store = ModelStore(settings.data_dir)
    needed = models_for_profile(report.profile)
    installed = {s.spec.id for s in store.statuses() if s.state == "installed"}
    data: dict[str, Any] = {
        "server": {"url": url, "reachable": False, "error": None},
        "enrolled": key is not None,
        "worker": identity.model_dump() if identity else None,
        "backlog": None,
        "totals": {"files": 0, "hours": 0.0, "jobs": 0},
        "estimate": None,
        "hardware": {
            "gpu": gpu.name if gpu else None,
            "vram_total_mb": gpu.vram_total_mb if gpu else None,
            "vram_free_mb": gpu.vram_free_mb if gpu else None,
            "backend": report.backend.value,
            "profile": report.profile.value,
            "cpu": report.cpu.model,
            "cores": report.cpu.cores,
        },
        "models": {
            "needed": needed,
            "installed": sorted(installed & set(needed)),
            "missing": [m for m in needed if m not in installed],
        },
        "runnable_stages": [s.value for s in runnable],
        "mount": None,
        "last_run": load_last_run(settings),
        "issues": issues,
    }
    if url is None:
        issues.append({"code": "no_server", "message": "No server configured."})
    elif key is None and load_user_token(settings) is None:
        issues.append({"code": "not_enrolled", "message": "This PC is not enrolled yet."})
    else:
        try:
            backlog = fetch_backlog(settings)
            data["server"]["reachable"] = True
            data["backlog"] = backlog
            data["totals"] = _totals(backlog)
            data["estimate"] = throughput.estimate(
                backlog, throughput.load_history(settings), settings.effective_concurrency
            )
        except UserApiError as exc:
            data["server"]["error"] = str(exc)
            issues.append({"code": "server_unreachable", "message": str(exc)})
    if probe_mount and data["server"]["reachable"] and key is not None:
        data["mount"] = _mount_state(settings)
        if not data["mount"]["ok"]:
            issues.append({"code": "mount", "message": str(data["mount"]["error"])})
    if data["models"]["missing"]:
        issues.append({"code": "models_missing",
                       "message": f"{len(data['models']['missing'])} AI model(s) not installed."})  # fmt: skip
    data["ready"] = not [i for i in issues if i["code"] != "models_missing"]
    return data
