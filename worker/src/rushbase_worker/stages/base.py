"""The stage handler protocol and the context handlers run in."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from rushbase_core import Stage, relative_to_root, resolve_in_root
from rushbase_core.layout import DERIVED_DIR, SYSTEM_DIR
from rushbase_worker.config import WorkerSettings
from rushbase_worker.models import Job
from rushbase_worker.runtimes.manager import Runtimes
from rushbase_worker.taxonomy import TaxonomySource
from rushbase_worker.tools.toolbox import Toolbox


class StageError(Exception):
    """A stage failed. ``retryable=False`` marks failures a retry cannot fix."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class Requirements:
    """What a worker needs to run a stage; unmet requirements remove it from the capabilities."""

    tools: tuple[str, ...] = ()
    gpu: bool = False
    ai: bool = False
    """AI stage: advertised only when its backends and models are installed (or downloadable)."""


@dataclass(frozen=True)
class StageContext:
    """Everything a handler needs besides the job."""

    settings: WorkerSettings
    toolbox: Toolbox
    library_root: Path
    output_root: Path | None = None
    """Where ``.rushbase/derived`` lives; ``None`` = the library root (set by `analyze`)."""
    runtimes: Runtimes | None = None
    """Inference runtimes (AI stages only)."""
    taxonomy: TaxonomySource | None = None
    """Approved taxonomy snapshot source (vision and classify)."""

    def need_runtimes(self) -> Runtimes:
        """The AI runtimes; a missing container is a deterministic setup error."""
        if self.runtimes is None:
            raise StageError("AI runtimes are not configured for this run", retryable=False)
        return self.runtimes

    def source(self, rel_path: str) -> Path:
        """Local path of a library-relative file."""
        return resolve_in_root(self.library_root, rel_path)

    def derived_dir(self, asset_id: UUID) -> Path:
        """``.rushbase/derived/<asset-id>/`` (created on demand); the only place workers write."""
        directory = (
            (self.output_root or self.library_root) / SYSTEM_DIR / DERIVED_DIR / str(asset_id)
        )
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def derived_path(self, rel_path: str) -> Path:
        """Local path of a derived file given its output-relative path."""
        return resolve_in_root(self.output_root or self.library_root, rel_path)

    def rel(self, path: Path) -> str:
        """Library-relative ``/`` path of ``path``."""
        return relative_to_root(self.output_root or self.library_root, path)


class StageHandler(Protocol):
    """One processing stage."""

    stage: Stage
    requirements: Requirements

    async def run(self, job: Job, ctx: StageContext) -> BaseModel:
        """Process ``job`` and return the stage output (validated against the contract)."""
        ...
