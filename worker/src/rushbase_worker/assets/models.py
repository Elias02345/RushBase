"""Model store: ``<data_dir>/models/<model-id>/`` with verified, atomically installed files."""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from rushbase_worker.assets.download import ProgressSink, download, sha256_file
from rushbase_worker.assets.lock import ModelSpec, load_models_lock

State = Literal["missing", "partial", "installed"]
MARKER = ".rushbase-model.json"


@dataclass(frozen=True)
class ModelStatus:
    """What is on disk for one pinned model."""

    spec: ModelSpec
    state: State
    path: Path
    disk_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.spec.id,
            "kind": self.spec.kind,
            "display": self.spec.display,
            "license": self.spec.license,
            "revision": self.spec.revision,
            "state": self.state,
            "size": self.spec.size,
            "disk_bytes": self.disk_bytes,
            "path": str(self.path),
        }


class ModelNotInstalledError(Exception):
    """A stage needs a model that has not been pulled."""

    def __init__(self, model_id: str) -> None:
        super().__init__(
            f"model {model_id!r} is not installed; run `rushbase models pull` "
            f"(or `rushbase models pull --profile <xl|gpu8|gpu4|cpu>`)"
        )
        self.model_id = model_id


class ModelStore:
    """Resolves, installs, verifies and removes pinned models."""

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "models"

    def path(self, model_id: str) -> Path:
        return self.root / model_id

    def file(self, model_id: str, role: str) -> Path:
        """Path of an installed model file; raises :class:`ModelNotInstalledError` if absent."""
        spec = load_models_lock().get(model_id)
        if self.status(spec).state != "installed":
            raise ModelNotInstalledError(model_id)
        return self.path(model_id) / spec.file(role).local_name

    def status(self, spec: ModelSpec) -> ModelStatus:
        """Cheap state check (marker + file sizes; no hashing)."""
        directory = self.path(spec.id)
        present = [directory / f.local_name for f in spec.files]
        sizes = [p.stat().st_size if p.is_file() else None for p in present]
        disk = sum(s for s in sizes if s is not None)
        complete = all(
            size == f.size for size, f in zip(sizes, spec.files, strict=True)
        ) and self._marker_matches(directory, spec)
        state: State = (
            "installed" if complete else ("partial" if disk or directory.is_dir() else "missing")
        )
        return ModelStatus(spec, state, directory, disk)

    def statuses(self) -> list[ModelStatus]:
        return [self.status(spec) for spec in load_models_lock().all()]

    def pull(
        self,
        spec: ModelSpec,
        emit: ProgressSink,
        client: httpx.Client | None = None,
        roles: tuple[str, ...] | None = None,
    ) -> ModelStatus:
        """Download the files of ``spec`` (resumable) and write the install marker last.

        ``roles`` limits the download to some files (the server needs only the text towers of the
        embedding models); the model then shows as ``partial`` until the rest is pulled.
        """
        wanted = tuple(f for f in spec.files if roles is None or f.role in roles)
        missing_roles = set(roles or ()) - {f.role for f in spec.files}
        if missing_roles:
            raise KeyError(f"{spec.id} has no file with role(s) {', '.join(sorted(missing_roles))}")
        directory = self.path(spec.id)
        directory.mkdir(parents=True, exist_ok=True)
        emit({"event": "model_started", "id": spec.id, "size": sum(f.size for f in wanted)})
        for file in wanted:
            download(file.url, directory / file.local_name, sha256=file.sha256, size=file.size,
                     label=f"{spec.id}/{file.local_name}", emit=emit, client=client)  # fmt: skip
        (directory / MARKER).write_text(
            json.dumps({"id": spec.id, "revision": spec.revision,
                        "files": {f.local_name: f.sha256 for f in wanted}}, indent=2)
        )  # fmt: skip
        emit({"event": "model_done", "id": spec.id})
        return self.status(spec)

    def verify(self, spec: ModelSpec, emit: ProgressSink) -> list[str]:
        """Re-hash every installed file; returns problems (empty = intact)."""
        problems: list[str] = []
        directory = self.path(spec.id)
        for file in spec.files:
            target = directory / file.local_name
            if not target.is_file():
                problems.append(f"{file.local_name}: missing")
                continue
            emit({"event": "verify_started", "file": f"{spec.id}/{file.local_name}"})
            if sha256_file(target) != file.sha256:
                problems.append(f"{file.local_name}: SHA-256 mismatch")
        return problems

    def remove(self, spec: ModelSpec) -> bool:
        """Delete the model directory; returns whether anything was removed."""
        directory = self.path(spec.id)
        if not directory.exists():
            return False
        shutil.rmtree(directory)
        return True

    @staticmethod
    def _marker_matches(directory: Path, spec: ModelSpec) -> bool:
        try:
            marker = json.loads((directory / MARKER).read_text())
        except (OSError, json.JSONDecodeError):
            return False
        return bool(marker.get("revision") == spec.revision)
