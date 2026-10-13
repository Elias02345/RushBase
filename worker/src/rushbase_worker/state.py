"""Local worker state in the data directory: API key (mode 600), identity and last run."""

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from rushbase_worker.config import WorkerSettings
from rushbase_worker.models import Enrolled

LAST_RUN_FILE = "last_run.json"


class Identity(BaseModel):
    """Non-secret facts written at enrollment (``worker.json``)."""

    worker_id: str
    name: str
    server_url: str
    library_id: str | None
    enrolled_at: str


def write_private(path: Path, text: str) -> None:
    """Atomically write ``text`` with mode 600 (the file never exists with wider permissions)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        Path(tmp_name).chmod(0o600)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def save_enrollment(
    settings: WorkerSettings, enrolled: Enrolled, name: str, server_url: str
) -> Identity:
    """Persist the API key and the worker identity."""
    write_private(settings.resolved_api_key_file, enrolled.api_key + "\n")
    identity = Identity(
        worker_id=str(enrolled.worker_id),
        name=name,
        server_url=server_url,
        library_id=str(enrolled.library_id) if enrolled.library_id else None,
        enrolled_at=datetime.now(UTC).isoformat(),
    )
    write_private(settings.state_file, identity.model_dump_json(indent=2) + "\n")
    return identity


def load_api_key(settings: WorkerSettings) -> str | None:
    """The stored API key or ``None`` when the worker is not enrolled."""
    try:
        key = settings.resolved_api_key_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return key or None


def load_identity(settings: WorkerSettings) -> Identity | None:
    """The stored identity or ``None``."""
    try:
        return Identity.model_validate_json(settings.state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_last_run(settings: WorkerSettings, document: dict[str, Any]) -> None:
    """Record the outcome of the latest ``process`` run."""
    write_private(settings.data_dir / LAST_RUN_FILE, json.dumps(document, indent=2) + "\n")


def load_last_run(settings: WorkerSettings) -> dict[str, Any] | None:
    """The latest run record or ``None``."""
    try:
        loaded = json.loads((settings.data_dir / LAST_RUN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None
