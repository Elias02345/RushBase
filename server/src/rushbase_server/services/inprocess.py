"""Optional in-process worker so a setup without any remote worker still makes progress.

Disabled by default (``RUSHBASE_INPROCESS_STAGES`` empty). Only stages with a server-side
implementation can be enabled: ``hash`` today; ``probe`` joins with the worker engine in M2.
The server reads originals itself here, which is acceptable for hashing on the host that
mounts the share anyway, but remote workers remain the intended path for heavy stages.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Final

import blake3
from sqlalchemy import select

from rushbase_core import Stage, resolve_in_root
from rushbase_server.db import SessionFactory
from rushbase_server.models import AssetFile
from rushbase_server.services import queue

logger = logging.getLogger(__name__)

SUPPORTED_STAGES: Final = frozenset({Stage.HASH})
_CHUNK: Final = 4 * 1024 * 1024


def hash_files(root: Path, rel_paths: list[str]) -> list[dict[str, str | int]]:
    """Return ``HashOutput`` file entries (path, size, full BLAKE3 hex digest) per file."""
    entries: list[dict[str, str | int]] = []
    for rel in rel_paths:
        hasher = blake3.blake3(max_threads=blake3.blake3.AUTO)
        size = 0
        with resolve_in_root(root, rel).open("rb") as handle:
            while chunk := handle.read(_CHUNK):
                hasher.update(chunk)
                size += len(chunk)
        entries.append({"rel_path": rel, "size": size, "blake3": hasher.hexdigest()})
    return entries


async def run_one(
    factory: SessionFactory,
    root: Path,
    stages: list[Stage],
    clock_now: Callable[[], datetime],
    config: queue.QueueConfig,
) -> bool:
    """Claim and execute one job of ``stages``; returns whether a job was processed."""
    runnable = [s for s in stages if s in SUPPORTED_STAGES]
    if not runnable:
        return False
    async with factory() as session:
        job = await queue.claim_server_job(session, runnable, clock_now(), config)
        if job is None:
            return False
        job_id, asset_id = job.id, job.asset_id
        rel_paths = list(
            (
                await session.execute(
                    select(AssetFile.rel_path).where(AssetFile.asset_id == asset_id)
                )
            )
            .scalars()
            .all()
        )
        await session.commit()
    started = clock_now()
    began = time.monotonic()
    try:
        entries = await asyncio.to_thread(hash_files, root, rel_paths)
        data = queue.ResultData(
            ok=True,
            output={"schema": 1, "files": entries},
            model_id="blake3",
            model_version=blake3.__version__,
            duration_ms=int((time.monotonic() - began) * 1000),
            started_at=started,
        )
    except OSError as exc:
        logger.warning("in-process hash failed", extra={"job": str(job_id), "error": str(exc)})
        data = queue.ResultData(ok=False, error=f"cannot read file: {exc}")
    async with factory() as session:
        await queue.submit_result(session, job_id, None, data, clock_now(), config)
        await session.commit()
    return True
