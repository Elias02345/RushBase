"""``hash`` stage: streaming BLAKE3 of every file of the asset."""

import asyncio
from pathlib import Path

from blake3 import blake3
from pydantic import BaseModel

from rushbase_core import Stage
from rushbase_core.results import FileHash, HashOutput
from rushbase_worker.models import Job
from rushbase_worker.stages.base import Requirements, StageContext, StageError

CHUNK_BYTES = 1024 * 1024


def hash_file(path: Path) -> tuple[int, str]:
    """Return ``(size, blake3 hex digest)``, reading in 1 MiB chunks."""
    hasher = blake3()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            hasher.update(chunk)
            size += len(chunk)
    return size, hasher.hexdigest()


class HashStage:
    """Content hashes for duplicate detection and integrity (never loads a file into memory)."""

    stage = Stage.HASH
    requirements = Requirements()

    async def run(self, job: Job, ctx: StageContext) -> BaseModel:
        """Hash every file of the asset."""
        files: list[FileHash] = []
        for file in job.files:
            path = ctx.source(file.rel_path)
            try:
                size, digest = await asyncio.to_thread(hash_file, path)
            except OSError as exc:
                raise StageError(f"cannot read {file.rel_path}: {exc.strerror or exc}") from exc
            files.append(FileHash(rel_path=file.rel_path, size=size, blake3=digest))
        return HashOutput(files=files)
