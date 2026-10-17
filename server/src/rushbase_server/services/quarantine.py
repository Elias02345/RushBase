"""Quarantine: parking the files of permanently failed assets and bringing them back.

When a job fails for good the queue only marks the asset ``quarantined``. This service then
moves its files (or its camera card directory, if it is the only member) to
``.rushbase/quarantine/<asset-id>/`` keeping the relative structure, and writes ``error.json``.
Retrying moves everything back to the original ``Input/`` location (if still free) and
re-creates the job plan. Moves use the same rename-only, never-overwrite helper as filing.
"""

import asyncio
import json
import logging
import os
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core import SYSTEM_DIR, resolve_in_root
from rushbase_core.fsops import (
    FileInUseError,
    append_line,
    atomic_write_bytes,
    lexists,
    safe_rename,
)
from rushbase_core.layout import JOURNAL_DIR, QUARANTINE_DIR
from rushbase_server.db import SessionFactory
from rushbase_server.models import Asset, AssetFile, Batch, InputFile, Job, MoveJournal
from rushbase_server.services import audit, queue
from rushbase_server.services.filing import card_siblings

logger = logging.getLogger(__name__)

ERROR_FILE = "error.json"


class QuarantineError(Exception):
    """A quarantine operation cannot be carried out (unknown asset, path occupied, ...)."""


@dataclass(frozen=True)
class QuarantineMove:
    """A performed move into quarantine (``kind`` is ``file`` or ``dir``)."""

    kind: str
    src: str
    dst: str


def quarantine_dir(asset_id: UUID) -> str:
    """Library-relative quarantine folder of an asset."""
    return f"{SYSTEM_DIR}/{QUARANTINE_DIR}/{asset_id}"


def execute_moves(
    root: Path, moves: list[QuarantineMove]
) -> tuple[list[QuarantineMove], list[str]]:
    """Perform ``moves``; returns ``(done, missing_sources)``. Undoes everything on errors."""
    done: list[QuarantineMove] = []
    missing: list[str] = []
    try:
        for move in moves:
            source = resolve_in_root(root, move.src)
            if not lexists(source):
                missing.append(move.src)
                continue
            safe_rename(source, resolve_in_root(root, move.dst))
            done.append(move)
    except BaseException:
        undo_moves(root, done)
        raise
    return done, missing


def undo_moves(root: Path, moves: list[QuarantineMove]) -> None:
    for move in reversed(moves):
        try:
            safe_rename(resolve_in_root(root, move.dst), resolve_in_root(root, move.src))
        except OSError:
            continue


def prune_empty(root: Path, rel_dir: str) -> None:
    """Remove empty directories below ``rel_dir`` and ``rel_dir`` itself (best effort)."""
    top = resolve_in_root(root, rel_dir)
    for directory, _subdirs, _names in os.walk(top, topdown=False):
        try:
            Path(directory).rmdir()
        except OSError:
            continue


async def _failure_of(session: AsyncSession, asset_id: UUID) -> dict[str, Any]:
    job = (
        await session.execute(
            select(Job)
            .where(Job.asset_id == asset_id, Job.state == "failed")
            .order_by(Job.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if job is None:
        return {"stage": None, "attempts": 0, "reason": "unknown"}
    return {"stage": job.stage, "attempts": job.attempts, "reason": job.last_error or "unknown"}


async def _plan_moves(
    session: AsyncSession, asset: Asset, files: list[AssetFile]
) -> list[QuarantineMove]:
    target = quarantine_dir(asset.id)
    root = next((f.card_structure_root for f in files if f.card_structure_root), None)
    if root is not None:
        siblings = await card_siblings(session, root)
        if all(s.id == asset.id for s in siblings):
            return [QuarantineMove("dir", root, f"{target}/{root}")]
    return [QuarantineMove("file", f.rel_path, f"{target}/{f.rel_path}") for f in files]


async def quarantine_asset(session: AsyncSession, root: Path, asset: Asset, now: datetime) -> bool:
    """Move the files of one quarantined asset out of ``Input/``; returns whether it moved.

    Busy files (``FileInUseError``) are left for the next maintenance pass.
    """
    files = list(
        (await session.execute(select(AssetFile).where(AssetFile.asset_id == asset.id)))
        .scalars()
        .all()
    )
    failure = await _failure_of(session, asset.id)
    planned = await _plan_moves(session, asset, files)
    try:
        done, missing = await asyncio.to_thread(execute_moves, root, planned)
    except FileInUseError as exc:
        logger.warning("quarantine postponed", extra={"asset": str(asset.id), "error": str(exc)})
        return False
    except OSError as exc:
        logger.error("quarantine failed", extra={"asset": str(asset.id), "error": str(exc)})
        return False
    for file in files:
        for move in done:
            if move.kind == "dir" and file.rel_path.startswith(move.src + "/"):
                file.rel_path = move.dst + file.rel_path[len(move.src) :]
                file.card_structure_root = move.dst
            elif move.kind == "file" and file.rel_path == move.src:
                file.rel_path = move.dst
    record = {
        **failure,
        "at": now.isoformat(),
        "moves": [vars(m) for m in done],
        "missing": missing,
    }
    asset.quarantine = record
    for move in done:
        session.add(
            MoveJournal(
                asset_id=asset.id, from_rel=move.src, to_rel=move.dst, at=now, reason="quarantine"
            )
        )
    audit.record(
        session,
        actor_kind="system",
        action="asset.quarantined",
        target_kind="asset",
        target_id=asset.id,
        details={"reason": str(failure["reason"])[:300], "stage": failure["stage"]},
    )
    error = {"asset_id": str(asset.id), **record}
    await asyncio.to_thread(
        atomic_write_bytes,
        resolve_in_root(root, f"{quarantine_dir(asset.id)}/{ERROR_FILE}"),
        (json.dumps(error, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )
    journal = root / SYSTEM_DIR / JOURNAL_DIR / f"{now:%Y-%m}.jsonl"
    for move in done:
        line = json.dumps(
            {
                "at": now.isoformat(),
                "kind": move.kind,
                "asset_id": str(asset.id),
                "from": move.src,
                "to": move.dst,
                "reason": "quarantine",
            }
        )
        await asyncio.to_thread(append_line, journal, line)
    return True


async def process_pending(
    factory: SessionFactory, root: Path, now_fn: Callable[[], datetime]
) -> int:
    """Quarantine every ``quarantined`` asset whose files have not been moved yet."""
    async with factory() as session:
        pending = list(
            (
                await session.execute(
                    select(Asset.id).where(
                        Asset.status == "quarantined", Asset.quarantine.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
    moved = 0
    for asset_id in pending:
        async with factory() as session:
            asset = await session.get_one(Asset, asset_id)
            if await quarantine_asset(session, root, asset, now_fn()):
                await session.commit()
                moved += 1
    return moved


async def retry_asset(
    session: AsyncSession,
    root: Path,
    asset_id: UUID,
    stages: Collection[str],
    config: queue.QueueConfig,
    now: datetime,
    user_id: UUID | None = None,
) -> None:
    """Move a quarantined asset back to ``Input/`` and restart its processing.

    Raises:
        QuarantineError: the asset is not in quarantine, its files are gone, or an original
            location is occupied (nothing is moved in that case).
    """
    asset = await session.get(Asset, asset_id)
    if asset is None or asset.status != "quarantined":
        raise QuarantineError("asset is not quarantined")
    record = asset.quarantine
    if record is None:
        raise QuarantineError("the files of this asset have not been moved to quarantine yet")
    moves = [QuarantineMove(**m) for m in record.get("moves", [])]
    for move in moves:
        if lexists(resolve_in_root(root, move.src)):
            raise QuarantineError(f"original location is occupied: {move.src}")
        if not lexists(resolve_in_root(root, move.dst)):
            raise QuarantineError(f"quarantined item is missing: {move.dst}")
    back = [QuarantineMove(m.kind, m.dst, m.src) for m in moves]
    try:
        await asyncio.to_thread(execute_moves, root, back)
    except OSError as exc:
        raise QuarantineError(f"could not move files back: {exc}") from exc
    files = list(
        (await session.execute(select(AssetFile).where(AssetFile.asset_id == asset.id)))
        .scalars()
        .all()
    )
    restored_roots = {m.dst: m.src for m in moves if m.kind == "dir"}
    for file in files:
        file.rel_path = file.original_rel_path
        if file.card_structure_root in restored_roots:
            file.card_structure_root = restored_roots[file.card_structure_root]
    await session.execute(
        update(InputFile)
        .where(InputFile.rel_path.in_([f.original_rel_path for f in files]))
        .values(state="grouped", asset_id=asset.id)
    )
    for move in back:
        session.add(
            MoveJournal(
                asset_id=asset.id,
                from_rel=move.src,
                to_rel=move.dst,
                at=now,
                reason="quarantine_retry",
            )
        )
    asset.quarantine = None
    batch = await session.get(Batch, asset.batch_id) if asset.batch_id else None
    await queue.create_jobs_for_asset(session, asset, batch, stages, config)
    audit.record(
        session,
        actor_kind="user" if user_id else "system",
        actor_id=user_id,
        action="asset.quarantine_retry",
        target_kind="asset",
        target_id=asset.id,
    )
    await session.flush()
    if asset.batch_id is not None:
        await queue.refresh_batch_status(session, asset.batch_id, now)
    await asyncio.to_thread(_clear_quarantine_dir, root, quarantine_dir(asset.id))


def _clear_quarantine_dir(root: Path, rel_dir: str) -> None:
    """Delete ``error.json`` and the (now empty) quarantine folder of a restored asset."""
    resolve_in_root(root, f"{rel_dir}/{ERROR_FILE}").unlink(missing_ok=True)
    prune_empty(root, rel_dir)


async def list_quarantined(session: AsyncSession) -> list[tuple[Asset, str]]:
    """Quarantined assets with their primary file name, newest first."""
    rows = await session.execute(
        select(Asset, AssetFile.original_rel_path)
        .outerjoin(AssetFile, AssetFile.id == Asset.primary_file_id)
        .where(Asset.status == "quarantined")
        .order_by(Asset.id.desc())
    )
    return [(asset, (path or "").rsplit("/", 1)[-1]) for asset, path in rows.all()]
