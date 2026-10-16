"""Resolve bridge: runs INSIDE the interpreter that can import ``DaVinciResolveScript``.

It is executed as a script (``ResolvePython bridge.py``), reads one JSON request from stdin and
prints one JSON response to stdout. Standard library only: it must not import ``rushbase_*``
because ResolvePython (21.1+) is a separate interpreter. See ADR-0002.

Requests: ``{"op": "probe" | "push" | "selftest", ...}``. Every Resolve call's return value is
checked; failures are collected in ``errors`` and never raised silently.
"""

import json
import sys
from typing import Any

THIRD_PARTY_KEY = "rushbase_asset_id"
MARKER_PREFIX = "rushbase:"
DEFAULT_METADATA_KEYS = ("Description", "Keywords", "Comments", "Scene", "Shot", "Take")


class BridgeError(Exception):
    """A Resolve precondition failed (not running, no project, wrong project...)."""


def _connect() -> Any:
    try:
        import DaVinciResolveScript as dvr  # type: ignore[import-not-found]
    except ImportError as exc:
        raise BridgeError(f"DaVinciResolveScript is not importable: {exc}") from exc
    resolve = dvr.scriptapp("Resolve")
    if not resolve:
        raise BridgeError(
            "Resolve is not reachable: start DaVinci Resolve Studio and set "
            "Preferences > System > General > External scripting using to Local"
        )
    return resolve


def _folder_by_name(folder: Any, name: str) -> Any | None:
    for sub in folder.GetSubFolderList() or []:
        if sub.GetName() == name:
            return sub
    return None


def probe(_request: dict[str, Any]) -> dict[str, Any]:
    """Version, edition and current project (read-only)."""
    resolve = _connect()
    product = str(resolve.GetProductName() or "")
    project = resolve.GetProjectManager().GetCurrentProject()
    return {
        "product": product,
        "version": str(resolve.GetVersionString() or ""),
        "studio": "studio" in product.lower(),
        "project": project.GetName() if project else None,
    }


def _ensure_bin(pool: Any, name: str, errors: list[str]) -> Any | None:
    root = pool.GetRootFolder()
    folder = _folder_by_name(root, name)
    if folder is None:
        folder = pool.AddSubFolder(root, name)
    if not folder:
        errors.append(f"could not create bin {name!r}")
        return None
    if not pool.SetCurrentFolder(folder):
        errors.append(f"could not open bin {name!r}")
        return None
    return folder


def _find_existing(folder: Any, path: str, asset_id: str) -> Any | None:
    for item in folder.GetClipList() or []:
        if item.GetClipProperty("File Path") == path:
            return item
        third = item.GetThirdPartyMetadata(THIRD_PARTY_KEY)
        if third and str(third) == asset_id:
            return item
    return None


def _apply_metadata(
    item: Any, clip: dict[str, Any], keys: list[str], result: dict[str, Any]
) -> None:
    values = {k: v for k, v in (clip.get("metadata") or {}).items() if v}
    if values and not item.SetMetadata(values):
        result["errors"].append(f"{clip['path']}: SetMetadata rejected {sorted(values)}")
    elif values:
        result["metadata_set"] += 1
    if hasattr(item, "SetThirdPartyMetadata") and not item.SetThirdPartyMetadata(
        {THIRD_PARTY_KEY: clip["asset_id"]}
    ):
        result["errors"].append(f"{clip['path']}: SetThirdPartyMetadata failed")
    stored = item.GetMetadata()
    if isinstance(stored, dict):
        for key in stored:
            if key not in keys:
                keys.append(key)


def _apply_markers(item: Any, clip: dict[str, Any], result: dict[str, Any]) -> None:
    fps = float(clip.get("fps") or 25.0)
    for shot in clip.get("shots") or []:
        custom = f"{MARKER_PREFIX}{clip['asset_id']}:{shot['idx']}"
        if item.GetMarkerByCustomData(custom):
            continue
        frame = round(shot["start_ms"] * fps / 1000)
        length = max(1, round((shot["end_ms"] - shot["start_ms"]) * fps / 1000))
        name = f"Shot {shot['idx']}"
        note = shot.get("description") or ""
        if item.AddMarker(frame, clip.get("marker_color") or "Blue", name, note, length, custom):
            result["markers_added"] += 1
        else:
            result["errors"].append(f"{clip['path']}: AddMarker failed at frame {frame}")


def push(request: dict[str, Any]) -> dict[str, Any]:
    """Import clips into a bin; set metadata, markers and clip colour; idempotent."""
    resolve = _connect()
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        raise BridgeError("no project is open in Resolve")
    expected = request.get("project")
    if expected and project.GetName() != expected:
        raise BridgeError(f"the open project is {project.GetName()!r}, expected {expected!r}")
    pool = project.GetMediaPool()
    result: dict[str, Any] = {
        "project": project.GetName(), "bin": request["bin"], "imported": 0, "existing": 0,
        "metadata_set": 0, "markers_added": 0, "colored": 0, "keys": [], "errors": [],
    }  # fmt: skip
    folder = _ensure_bin(pool, request["bin"], result["errors"])
    if folder is None:
        return result
    keys: list[str] = list(request.get("known_keys") or [])
    for clip in request["clips"]:
        item = _find_existing(folder, clip["path"], clip["asset_id"])
        if item is not None:
            result["existing"] += 1
        else:
            imported = pool.ImportMedia([{"FilePath": clip["path"]}])
            if not imported:
                result["errors"].append(f"{clip['path']}: ImportMedia failed")
                continue
            item = imported[0]
            result["imported"] += 1
        if request.get("metadata", True):
            _apply_metadata(item, clip, keys, result)
        if request.get("markers") and clip.get("shots"):
            _apply_markers(item, clip, result)
        color = clip.get("clip_color")
        if color:
            if item.SetClipColor(color):
                result["colored"] += 1
            else:
                result["errors"].append(f"{clip['path']}: SetClipColor({color}) failed")
    result["keys"] = keys
    return result


def selftest(request: dict[str, Any]) -> dict[str, Any]:
    """Create or reuse the throwaway project, push the given clip and read everything back."""
    resolve = _connect()
    manager = resolve.GetProjectManager()
    name = request["project"]
    if not name.startswith("rushbase-test"):
        raise BridgeError("the self-test only uses projects whose name starts with 'rushbase-test'")
    previous = manager.GetCurrentProject()
    previous_name = previous.GetName() if previous else None
    if previous_name != name:
        if name in (manager.GetProjectListInCurrentFolder() or []):
            project = manager.LoadProject(name)
        else:
            project = manager.CreateProject(name)
        if not project:
            raise BridgeError(f"could not open or create the project {name!r}")
    result = push({**request, "project": name})
    checks: dict[str, bool] = {}
    project = manager.GetCurrentProject()
    folder = _folder_by_name(project.GetMediaPool().GetRootFolder(), request["bin"])
    items = list(folder.GetClipList() or []) if folder else []
    checks["imported"] = bool(items)
    if items:
        item = items[0]
        stored = item.GetMetadata()
        wanted = request["clips"][0].get("metadata") or {}
        checks["metadata"] = isinstance(stored, dict) and all(
            str(stored.get(k)) == str(v) for k, v in wanted.items() if v
        )
        checks["third_party"] = (
            str(item.GetThirdPartyMetadata(THIRD_PARTY_KEY)) == (request["clips"][0]["asset_id"])
        )
        if request.get("markers"):
            checks["markers"] = len(item.GetMarkers() or {}) >= len(request["clips"][0]["shots"])
    again = push({**request, "project": name})
    checks["idempotent"] = again["imported"] == 0 and again["markers_added"] == 0
    if previous_name and previous_name != name:
        manager.LoadProject(previous_name)
    return {"push": result, "checks": checks, "ok": all(checks.values()) and not result["errors"]}


OPERATIONS = {"probe": probe, "push": push, "selftest": selftest}


def main() -> int:
    """Entry point: JSON request on stdin, JSON response on stdout."""
    try:
        request = json.load(sys.stdin)
        data = OPERATIONS[request["op"]](request)
        document: dict[str, Any] = {"ok": True, "data": data}
    except BridgeError as exc:
        document = {"ok": False, "error": str(exc)}
    except Exception as exc:
        document = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(json.dumps(document) + "\n")
    return 0 if document["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
