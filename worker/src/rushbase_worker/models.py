"""Wire models of the worker API (``/api/v1/worker/*``), as the worker consumes them."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from rushbase_core import Stage


class _Wire(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class JobFile(_Wire):
    """A file of the job's asset (paths relative to the library root)."""

    role: str
    rel_path: str
    size: int
    card_structure_root: str | None = None


class Job(_Wire):
    """A leased job."""

    job_id: UUID
    asset_id: UUID
    stage: Stage
    media_type: str
    attempt: int = 1
    lease_expires_at: datetime
    files: list[JobFile]
    upstream: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def primary(self) -> JobFile:
        """The primary file (first file when no ``primary`` role exists)."""
        for file in self.files:
            if file.role == "primary":
                return file
        if not self.files:
            raise ValueError(f"job {self.job_id} has no files")
        return self.files[0]

    def files_with_role(self, *roles: str) -> list[JobFile]:
        """Files whose role is one of ``roles``."""
        return [f for f in self.files if f.role in roles]


class Claim(_Wire):
    """Response of the claim call."""

    jobs: list[Job]
    heartbeat_seconds: float
    lease_seconds: float


class Enrolled(_Wire):
    """Response of the enrollment call (the API key is shown once)."""

    worker_id: UUID
    api_key: str
    library_id: UUID | None
    heartbeat_seconds: float
    lease_seconds: float


class LibraryInfo(_Wire):
    """Response of ``GET /library``."""

    library_id: UUID | None
    name: str | None = None


class ResultAck(_Wire):
    """Response of the result call."""

    accepted: bool
    duplicate: bool
    job_state: str
