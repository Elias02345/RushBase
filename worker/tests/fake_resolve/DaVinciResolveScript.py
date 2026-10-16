"""A stateful stand-in for ``DaVinciResolveScript`` (state persisted in a JSON file).

Only the calls the Rushbase bridge uses are implemented; names and return types follow the
Resolve 21.1.1 stubs. The state file path comes from ``FAKE_RESOLVE_STATE``; ``FAKE_RESOLVE_MODE``
may be ``free`` (edition without scripting), ``down`` (scriptapp returns None) or
``reject-metadata`` (SetMetadata returns False).
"""

import json
import os
from pathlib import Path
from typing import Any

STATE = Path(os.environ.get("FAKE_RESOLVE_STATE", "fake-resolve.json"))
MODE = os.environ.get("FAKE_RESOLVE_MODE", "")


def _load() -> dict[str, Any]:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"current": "Untitled", "projects": ["Untitled"], "bins": {}, "items": {}}


def _save(state: dict[str, Any]) -> None:
    STATE.write_text(json.dumps(state))


class Item:
    def __init__(self, path: str) -> None:
        self.path = path

    def _mutate(self, key: str, value: Any) -> None:
        state = _load()
        state["items"][self.path][key] = value
        _save(state)

    def _get(self, key: str) -> Any:
        return _load()["items"][self.path][key]

    def GetClipProperty(self, name: str | None = None) -> Any:
        return self.path if name == "File Path" else {"File Path": self.path}

    def GetMetadata(self, key: str | None = None) -> Any:
        return self._get("metadata")

    def SetMetadata(self, values: dict[str, str]) -> bool:
        if MODE == "reject-metadata":
            return False
        self._mutate("metadata", {**self._get("metadata"), **values})
        return True

    def GetThirdPartyMetadata(self, key: str | None = None) -> Any:
        third = self._get("third")
        return third.get(key, "") if key else third

    def SetThirdPartyMetadata(self, values: dict[str, str]) -> bool:
        self._mutate("third", {**self._get("third"), **values})
        return True

    def AddMarker(self, frame: int, color: str, name: str, note: str, duration: int, custom: str | None = None) -> bool:
        markers = self._get("markers")
        markers[str(frame)] = {"color": color, "name": name, "note": note, "duration": duration, "customData": custom}
        self._mutate("markers", markers)
        return True

    def GetMarkers(self) -> dict[int, Any]:
        return {int(k): v for k, v in self._get("markers").items()}

    def GetMarkerByCustomData(self, custom: str) -> Any:
        return next((v for v in self._get("markers").values() if v["customData"] == custom), {})

    def SetClipColor(self, color: str) -> bool:
        self._mutate("color", color)
        return True


class Folder:
    def __init__(self, name: str) -> None:
        self.name = name

    def GetName(self) -> str:
        return self.name

    def GetSubFolderList(self) -> list["Folder"]:
        return [Folder(n) for n in _load()["bins"]] if self.name == "Master" else []

    def GetClipList(self) -> list[Item]:
        return [Item(p) for p in _load()["bins"].get(self.name, [])]


class Pool:
    def __init__(self) -> None:
        self.current = "Master"

    def GetRootFolder(self) -> Folder:
        return Folder("Master")

    def AddSubFolder(self, parent: Folder, name: str) -> Folder:
        state = _load()
        state["bins"].setdefault(name, [])
        _save(state)
        return Folder(name)

    def SetCurrentFolder(self, folder: Folder) -> bool:
        self.current = folder.name
        return True

    def ImportMedia(self, infos: list[dict[str, str]]) -> list[Item]:
        state = _load()
        items = []
        for info in infos:
            path = info["FilePath"]
            if not Path(path).exists():
                return []
            state["items"].setdefault(path, {"metadata": {}, "third": {}, "markers": {}, "color": ""})
            state["bins"].setdefault(self.current, []).append(path)
            items.append(Item(path))
        _save(state)
        return items


class Project:
    def __init__(self, name: str) -> None:
        self.name = name

    def GetName(self) -> str:
        return self.name

    def GetMediaPool(self) -> Pool:
        return Pool()


class Manager:
    def GetCurrentProject(self) -> Project:
        return Project(_load()["current"])

    def GetProjectListInCurrentFolder(self) -> list[str]:
        return _load()["projects"]

    def CreateProject(self, name: str) -> Project:
        state = _load()
        state["projects"].append(name)
        state["current"] = name
        state["bins"], state["items"] = {}, {}
        _save(state)
        return Project(name)

    def LoadProject(self, name: str) -> Project | None:
        state = _load()
        if name not in state["projects"]:
            return None
        state["current"] = name
        _save(state)
        return Project(name)


class Resolve:
    def GetProductName(self) -> str:
        return "DaVinci Resolve" if MODE == "free" else "DaVinci Resolve Studio"

    def GetVersionString(self) -> str:
        return "21.1.1.0003"

    def GetProjectManager(self) -> Manager:
        return Manager()


def scriptapp(name: str) -> Resolve | None:
    return None if MODE == "down" else Resolve()
