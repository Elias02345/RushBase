"""The ``finalize`` stage: summaries, filing rule, rename into ``Content/``, sidecars, journal.

The server is the single writer of ``Content/`` (invariant 2). Moving is always a rename on the
same share, never a copy, never an overwrite; original names are kept. Camera card structures
(a directory that must stay intact) move as one directory rename once the last asset that lives
in it has been analysed. A failure while moving or documenting a unit undoes the whole unit.
"""

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core import SYSTEM_DIR, Stage
from rushbase_core.fsops import FileInUseError, append_line
from rushbase_core.layout import JOURNAL_DIR
from rushbase_core.sidecar import SidecarV1
from rushbase_core.summary import technical_summary
from rushbase_server.db import SessionFactory
from rushbase_server.models import (
    Asset,
    AssetFile,
    AssetMetadata,
    Batch,
    FilingRule,
    InputFile,
    MoveJournal,
    Shot,
)
from rushbase_server.services import (
    ai_sidecar,
    audit,
    duplicates,
    filing_rules,
    placement,
    queue,
    sidecars,
)
from rushbase_server.services.normalize import probe_from_documents

logger = logging.getLogger(__name__)

PENDING_STATUSES = ("ingesting", "processing")
"""Statuses of card siblings that still have analysis ahead of them."""


class FilingError(Exception):
    """Filing a unit failed; ``retryable`` tells the queue whether another attempt can help."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class FilingEnv:
    """Everything the filing service needs from the running server."""

    root: Path
    library_id: UUID | None
    path_budget: int
    central_meta: bool
    queue_config: queue.QueueConfig


@dataclass(frozen=True)
class FiledUnit:
    """Outcome of filing one unit."""

    asset_ids: tuple[UUID, ...]
    directory: str
    rule_version: int


# --- helpers -----------------------------------------------------------------------------


def _journal_path(now: datetime) -> str:
    return f"{SYSTEM_DIR}/{JOURNAL_DIR}/{now:%Y-%m}.jsonl"


async def _files_of(
    session: AsyncSession, asset_ids: Sequence[UUID]
) -> dict[UUID, list[AssetFile]]:
    rows = (
        (
            await session.execute(
                select(AssetFile).where(AssetFile.asset_id.in_(asset_ids)).order_by(AssetFile.id)
            )
        )
        .scalars()
        .all()
    )
    result: dict[UUID, list[AssetFile]] = defaultdict(list)
    for row in rows:
        result[row.asset_id].append(row)
    return result


def _card_root(files: Sequence[AssetFile]) -> str | None:
    return next((f.card_structure_root for f in files if f.card_structure_root), None)


async def card_siblings(session: AsyncSession, card_root: str) -> list[Asset]:
    """All assets that own files in the card structure ``card_root``."""
    ids = select(AssetFile.asset_id).where(AssetFile.card_structure_root == card_root)
    return list(
        (await session.execute(select(Asset).where(Asset.id.in_(ids)).order_by(Asset.id)))
        .scalars()
        .all()
    )


async def set_summaries(session: AsyncSession, asset: Asset) -> None:
    """Fill the German and English technical summaries from the stored metadata."""
    metadata = await session.get(AssetMetadata, asset.id)
    if metadata is None:
        return
    try:
        probe = probe_from_documents(metadata.normalized, metadata.raw)
    except ValueError:
        logger.warning(
            "stored metadata is not a valid probe document", extra={"asset": str(asset.id)}
        )
        return
    asset.tech_summary_de = technical_summary(probe, asset.media_type, "de") or None
    asset.tech_summary_en = technical_summary(probe, asset.media_type, "en") or None


# --- filing a unit -----------------------------------------------------------------------


def _fs_error(exc: OSError) -> FilingError:
    if isinstance(exc, FileNotFoundError):
        return FilingError(f"file vanished before filing: {exc.filename or exc}", retryable=False)
    if isinstance(exc, FileInUseError):
        return FilingError(f"file is in use, will retry: {exc}", retryable=True)
    return FilingError(f"file system error while filing: {exc}", retryable=True)


def sidecar_tasks(
    unit: Sequence[Asset],
    files: dict[UUID, list[AssetFile]],
    documents: dict[UUID, SidecarV1],
    directory: str,
) -> list[sidecars.SidecarTask]:
    tasks = []
    for asset in unit:
        asset_files = files[asset.id]
        primary = next((f for f in asset_files if f.id == asset.primary_file_id), asset_files[0])
        name = PurePosixPath(primary.rel_path)
        task = sidecars.SidecarTask(
            asset_id=asset.id,
            sidecar=documents[asset.id],
            meta_directory=asset.content_rel_dir or directory,
            filename=name.name,
        )
        if asset.media_type == "photo":
            camera_xmp = any(
                f.role == "sidecar"
                and PurePosixPath(f.rel_path).suffix.lower() == ".xmp"
                and PurePosixPath(f.rel_path).stem == name.stem
                for f in asset_files
            )
            task = sidecars.SidecarTask(
                asset_id=task.asset_id,
                sidecar=task.sidecar,
                meta_directory=task.meta_directory,
                filename=task.filename,
                xmp_directory=str(name.parent),
                xmp_stem=name.stem,
                camera_xmp_present=camera_xmp,
            )
        tasks.append(task)
    return tasks


def _journal_lines(
    now: datetime,
    anchor: UUID,
    spec: placement.UnitSpec,
    result: placement.Placement,
    owner: dict[str, UUID],
) -> list[str]:
    """JSONL lines for a unit: one for a renamed directory, one per file otherwise."""
    if spec.card_root is not None and spec.whole_directory and result.card_root is not None:
        entries = [("dir", anchor, spec.card_root, result.card_root)]
    else:
        entries = [
            ("file", owner[m.file_id], m.src, result.new_paths[m.file_id]) for m in spec.moves
        ]
    return [
        json.dumps(
            {
                "at": now.isoformat(),
                "kind": kind,
                "asset_id": str(asset_id),
                "from": src,
                "to": dst,
                "reason": "filed",
            },
            ensure_ascii=False,
        )
        for kind, asset_id, src, dst in entries
    ]


async def file_unit(
    session: AsyncSession, env: FilingEnv, unit: Sequence[Asset], now: datetime
) -> FiledUnit:
    """Move ``unit`` into ``Content/``, write its sidecars and journal entries, and commit.

    ``unit`` is one asset, or all analysed assets of one camera card structure. Everything
    happens or nothing does: on failure the renames and sidecars are undone and the
    transaction is rolled back before :class:`FilingError` is raised.
    """
    ids = [a.id for a in unit]
    anchor = unit[0]
    files = await _files_of(session, ids)
    batch = await session.get(Batch, anchor.batch_id) if anchor.batch_id else None
    rule = await filing_rules.ensure_default_rule(session)
    card_root = _card_root([f for a in unit for f in files[a.id]])
    whole = False
    if card_root is not None:
        inside = (
            await session.execute(
                select(AssetFile.asset_id).where(
                    or_(
                        AssetFile.card_structure_root == card_root,
                        AssetFile.rel_path.startswith(card_root + "/", autoescape=True),
                    )
                )
            )
        ).scalars()
        whole = set(inside) <= set(ids)
    spec = placement.UnitSpec(
        moves=[placement.MoveSpec(str(f.id), f.rel_path) for a in unit for f in files[a.id]],
        card_root=card_root,
        whole_directory=whole,
    )
    ctx = filing_rules.context_for(anchor, batch)
    plan = await asyncio.to_thread(
        filing_rules.plan_for,
        env.root,
        rule.template,
        rule.fallbacks,
        ctx,
        placement.tail_length_for(spec),
        env.path_budget,
    )
    try:
        result = await asyncio.to_thread(placement.place_unit, env.root, spec, plan.path)
    except OSError as exc:
        await session.rollback()
        raise _fs_error(exc) from exc
    written = sidecars.WrittenSidecars()
    try:
        owner = {str(f.id): f.asset_id for a in unit for f in files[a.id]}
        by_id = {str(f.id): f for a in unit for f in files[a.id]}
        for file_id, new_path in result.new_paths.items():
            by_id[file_id].rel_path = new_path
            if result.card_root is not None:
                by_id[file_id].card_structure_root = result.card_root
        for asset in unit:
            asset.status = "filed"
            asset.content_rel_dir = result.card_root or result.directory
            asset.filed_at = now
            asset.rule_version_id = rule.id
        await _record_filing(session, unit, files, spec, result, owner, rule, now)
        documents = {}
        for asset in unit:
            shot_rows = list(
                (await session.execute(select(Shot).where(Shot.asset_id == asset.id)))
                .scalars()
                .all()
            )
            tags, ai = await ai_sidecar.load_sections(session, asset, shot_rows)
            documents[asset.id] = sidecars.build_sidecar(
                asset,
                batch,
                files[asset.id],
                await session.get(AssetMetadata, asset.id),
                shot_rows,
                rule,
                env.library_id,
                now,
                tags,
                ai,
            )
        tasks = sidecar_tasks(unit, files, documents, result.directory)
        written = await asyncio.to_thread(
            sidecars.write_sidecars, env.root, tasks, env.central_meta
        )
        lines = _journal_lines(now, anchor.id, spec, result, owner)
        for line in lines:
            await asyncio.to_thread(append_line, env.root / _journal_path(now), line)
        await session.commit()
    except BaseException as exc:
        await session.rollback()
        await asyncio.to_thread(sidecars.remove_written, env.root, written)
        await asyncio.to_thread(placement.rollback, env.root, result)
        if isinstance(exc, OSError):
            raise _fs_error(exc) from exc
        raise
    return FiledUnit(tuple(ids), result.card_root or result.directory, rule.version)


async def _record_filing(
    session: AsyncSession,
    unit: Sequence[Asset],
    files: dict[UUID, list[AssetFile]],
    spec: placement.UnitSpec,
    result: placement.Placement,
    owner: dict[str, UUID],
    rule: FilingRule,
    now: datetime,
) -> None:
    """Database bookkeeping of a filed unit: journal rows, audit entries, input file links."""
    await session.execute(
        update(InputFile).where(InputFile.asset_id.in_([a.id for a in unit])).values(asset_id=None)
    )
    if spec.card_root is not None and spec.whole_directory and result.card_root is not None:
        session.add(
            MoveJournal(
                asset_id=unit[0].id,
                from_rel=spec.card_root,
                to_rel=result.card_root,
                at=now,
                reason="filed",
            )
        )
    else:
        for move in spec.moves:
            session.add(
                MoveJournal(
                    asset_id=owner[move.file_id],
                    from_rel=move.src,
                    to_rel=result.new_paths[move.file_id],
                    at=now,
                    reason="filed",
                )
            )
    for asset in unit:
        audit.record(
            session,
            actor_kind="system",
            action="asset.filed",
            target_kind="asset",
            target_id=asset.id,
            details={
                "directory": asset.content_rel_dir,
                "rule_version": rule.version,
                "from": [f.original_rel_path for f in files[asset.id]][:20],
            },
        )
    await session.flush()


# --- the finalize job --------------------------------------------------------------------


async def _finalize_asset(
    session: AsyncSession, env: FilingEnv, asset: Asset, now: datetime
) -> dict[str, Any]:
    """Run ``finalize`` for one asset; returns the stage output. Raises :class:`FilingError`."""
    if asset.status == "filed":
        return {"filed": True, "directory": asset.content_rel_dir, "already": True}
    if await duplicates.detect(session, asset):
        return {"filed": False, "duplicate_of": str(asset.duplicate_of)}
    await set_summaries(session, asset)
    files = (await _files_of(session, [asset.id]))[asset.id]
    card_root = _card_root(files)
    unit = [asset]
    if card_root is not None:
        siblings = [s for s in await card_siblings(session, card_root) if s.id != asset.id]
        waiting = [s for s in siblings if s.status in PENDING_STATUSES]
        if waiting:
            asset.status = "review"
            await session.commit()
            return {"filed": False, "waiting_for": len(waiting), "card_root": card_root}
        unit = sorted([asset, *(s for s in siblings if s.status == "review")], key=lambda a: a.id)
    filed = await file_unit(session, env, unit, now)
    return {
        "filed": True,
        "directory": filed.directory,
        "rule_version": filed.rule_version,
        "assets": [str(i) for i in filed.asset_ids],
    }


async def settle_cards(
    factory: SessionFactory, env: FilingEnv, now_fn: Callable[[], datetime]
) -> int:
    """File card structures whose members are all analysed, quarantined or duplicates.

    Needed because the last open member of a card may leave the pipeline without running
    ``finalize`` (quarantine, duplicate); the waiting members must not wait forever.
    """
    async with factory() as session:
        roots = (
            (
                await session.execute(
                    select(AssetFile.card_structure_root)
                    .join(Asset, Asset.id == AssetFile.asset_id)
                    .where(Asset.status == "review", AssetFile.card_structure_root.is_not(None))
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
    filed = 0
    for root in roots:
        if root is None:
            continue
        async with factory() as session:
            members = await card_siblings(session, root)
            if any(m.status in PENDING_STATUSES for m in members):
                continue
            unit = [m for m in members if m.status == "review"]
            if not unit:
                continue
            try:
                await file_unit(session, env, unit, now_fn())
            except FilingError as exc:
                logger.warning("card filing failed", extra={"card": root, "error": str(exc)})
                continue
            await _refresh_batches(session, unit, now_fn())
            await session.commit()
            filed += len(unit)
    return filed


async def _refresh_batches(session: AsyncSession, unit: Sequence[Asset], now: datetime) -> None:
    for batch_id in {a.batch_id for a in unit if a.batch_id is not None}:
        await queue.refresh_batch_status(session, batch_id, now)


async def run_finalize(
    factory: SessionFactory, env: FilingEnv, now_fn: Callable[[], datetime], limit: int = 100
) -> int:
    """Complete the available ``finalize`` jobs; returns how many were processed."""
    if await _paused(factory):
        return 0
    await settle_cards(factory, env, now_fn)
    done = 0
    for _ in range(limit):
        async with factory() as session:
            job = await queue.claim_server_job(
                session, [Stage.FINALIZE], now_fn(), env.queue_config
            )
            if job is None:
                break
            job_id, asset_id = job.id, job.asset_id
            await session.commit()
        async with factory() as session:
            asset = await session.get_one(Asset, asset_id)
            batch_id = asset.batch_id
            try:
                output = await _finalize_asset(session, env, asset, now_fn())
                data = queue.ResultData(ok=True, output=output, model_id="rushbase-filing")
            except FilingError as exc:
                logger.warning("finalize failed", extra={"asset": str(asset_id), "error": str(exc)})
                data = queue.ResultData(ok=False, error=str(exc), retryable=exc.retryable)
                await session.rollback()
            await queue.submit_result(session, job_id, None, data, now_fn(), env.queue_config)
            if batch_id is not None:
                await queue.refresh_batch_status(session, batch_id, now_fn())
            await session.commit()
        done += 1
    return done


async def _paused(factory: SessionFactory) -> bool:
    async with factory() as session:
        return await queue.is_paused(session)
