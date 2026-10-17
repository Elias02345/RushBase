"""Input scanner: walks ``Input/``, tracks copy stability and turns stable files into assets.

One scan pass (:func:`scan_once`) is a single database transaction owned by the caller. The
file system is only touched through :func:`walk_input` and the readability/fingerprint checks,
which run in worker threads so a slow network share never blocks the event loop.
"""

import asyncio
import logging
import os
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core import (
    INPUT_DIR,
    AssetGroup,
    Observation,
    Stage,
    batch_for,
    batch_root_of,
    evaluate,
    group_files,
    is_ignored,
    quick_fingerprint,
    uuid7,
)
from rushbase_server.models import Asset, AssetFile, Batch, InputFile
from rushbase_server.services import queue

logger = logging.getLogger(__name__)

READY_STATES = ("stable", "grouped")
LIVE_STATES = ("copying", "stable", "grouped")


class InputUnavailableError(Exception):
    """``Input/`` cannot be read (missing mount?); nothing may be concluded from the scan."""


@dataclass(frozen=True)
class ScanEntry:
    """One file found in ``Input/``."""

    rel_path: str
    size: int
    mtime: datetime


@dataclass(frozen=True)
class ScanConfig:
    """Stability thresholds (ADR-0005: 2 scans and 120 s by default)."""

    stable_scans: int = 2
    stable_seconds: float = 120


@dataclass
class ScanReport:
    """Counters of one scan pass."""

    seen: int = 0
    new: int = 0
    changed: int = 0
    gone: int = 0
    became_stable: int = 0
    assets_created: int = 0
    files_attached: int = 0
    complete: bool = True


def unsafe_name(name: str) -> bool:
    """File names with a backslash or control characters are never ingested.

    A ``\\`` is a path separator on Windows workers but an ordinary character on POSIX, so such
    names could alias other paths; control characters can forge log and UI output.
    """
    return "\\" in name or any(ord(char) < 32 or ord(char) == 127 for char in name)


def walk_input(root: Path) -> tuple[list[ScanEntry], bool]:
    """List the non-ignored regular files below ``<root>/Input``.

    Returns ``(entries, complete)``; ``complete`` is false when a sub directory could not be
    read, in which case the caller must not conclude that missing files are gone.

    Raises:
        InputUnavailableError: ``Input/`` itself is not readable.
    """
    top = root / INPUT_DIR
    entries: list[ScanEntry] = []
    complete = True
    stack: list[tuple[Path, str]] = [(top, INPUT_DIR)]
    while stack:
        directory, rel = stack.pop()
        try:
            with os.scandir(directory) as iterator:
                children = list(iterator)
        except OSError as exc:
            if directory == top:
                raise InputUnavailableError(f"cannot read {top}: {exc}") from exc
            logger.warning("cannot read directory", extra={"path": rel, "error": str(exc)})
            complete = False
            continue
        for child in children:
            child_rel = f"{rel}/{child.name}"
            if unsafe_name(child.name):
                logger.warning("skipping unsafe file name", extra={"path": ascii(child_rel)})
                continue
            if is_ignored(child_rel):
                continue
            try:
                if child.is_dir(follow_symlinks=False):
                    stack.append((Path(child.path), child_rel))
                elif child.is_file(follow_symlinks=False):
                    stat = child.stat(follow_symlinks=False)
                    mtime = datetime.fromtimestamp(stat.st_mtime, UTC)
                    entries.append(ScanEntry(child_rel, stat.st_size, mtime))
            except OSError as exc:
                logger.warning("cannot stat entry", extra={"path": child_rel, "error": str(exc)})
                complete = False
    return entries, complete


def _readable_fingerprint(path: Path) -> str | None:
    """Return the quick fingerprint if the file opens for reading, else ``None``."""
    try:
        return quick_fingerprint(path)
    except OSError:
        return None


async def scan_once(
    session: AsyncSession,
    root: Path,
    now: datetime,
    scan: ScanConfig,
    queue_config: queue.QueueConfig,
    stages: Collection[Stage | str],
) -> ScanReport:
    """Run one scan pass and group newly stable files into assets with their job DAGs.

    Raises:
        InputUnavailableError: ``Input/`` could not be read; the database is left untouched.
    """
    entries, complete = await asyncio.to_thread(walk_input, root)
    report = ScanReport(seen=len(entries), complete=complete)
    rows: dict[str, InputFile] = {
        f.rel_path: f for f in (await session.execute(select(InputFile))).scalars()
    }
    batches: dict[str, Batch] = {
        b.input_path: b for b in (await session.execute(select(Batch))).scalars()
    }
    seen: set[str] = set()
    touched: set[Batch] = set()

    for entry in entries:
        seen.add(entry.rel_path)
        row = rows.get(entry.rel_path)
        if row is None:
            batch = _batch_for_new_file(session, batches, entry.rel_path, now)
            if batch is None:
                continue
            row = InputFile(
                id=uuid7(),
                batch_id=batch.id,
                rel_path=entry.rel_path,
                size=entry.size,
                mtime=entry.mtime,
                stable_count=1,
                first_seen_at=now,
                last_seen_at=now,
                last_changed_at=now,
                state="copying",
            )
            session.add(row)
            rows[entry.rel_path] = row
            report.new += 1
            continue
        await _observe(row, entry, root, now, scan, report)

    if complete:
        for path, row in rows.items():
            if path not in seen and row.state in LIVE_STATES:
                row.state = "gone"
                report.gone += 1

    by_batch: dict[UUID, list[InputFile]] = defaultdict(list)
    for row in rows.values():
        if row.state in LIVE_STATES:
            by_batch[row.batch_id].append(row)
    batch_by_id = {b.id: b for b in batches.values()}
    for batch_id, batch_rows in by_batch.items():
        batch = batch_by_id[batch_id]
        created, attached = await _group_batch(
            session, batch, batch_rows, now, queue_config, stages
        )
        report.assets_created += created
        report.files_attached += attached
        touched.add(batch)
    await session.flush()
    for batch in touched | {b for b in batches.values() if b.status == "scanning"}:
        await queue.refresh_batch_status(session, batch.id, now)
    return report


def _batch_for_new_file(
    session: AsyncSession, batches: dict[str, Batch], rel_path: str, now: datetime
) -> Batch | None:
    hint = batch_for(rel_path, now.date())
    if hint is None:
        return None
    batch = batches.get(hint.input_path)
    if batch is None:
        batch = Batch(
            id=uuid7(),
            input_path=hint.input_path,
            label=hint.label,
            hint_date=hint.hint_date,
            status="scanning",
            first_seen_at=now,
        )
        session.add(batch)
        batches[hint.input_path] = batch
        logger.info("new batch", extra={"batch": hint.input_path})
    return batch


async def _observe(
    row: InputFile,
    entry: ScanEntry,
    root: Path,
    now: datetime,
    scan: ScanConfig,
    report: ScanReport,
) -> None:
    """Apply one sighting of a known file: update counters and promote it when stable."""
    previous: Observation | None = None
    if row.state != "gone":
        previous = Observation(row.size, row.mtime, row.stable_count, row.last_changed_at)
    verdict = evaluate(
        previous,
        entry.size,
        entry.mtime,
        now,
        required_scans=scan.stable_scans,
        window_seconds=scan.stable_seconds,
    )
    row.last_seen_at = now
    if verdict.changed:
        report.changed += 1
        if row.state == "grouped":
            logger.warning("file changed after grouping", extra={"path": row.rel_path})
        else:
            row.state = "copying"
            row.quick_fp = None
    row.size = entry.size
    row.mtime = entry.mtime
    row.stable_count = verdict.stable_count
    row.last_changed_at = verdict.last_changed_at
    if verdict.stable and row.state == "copying":
        fingerprint = await asyncio.to_thread(_readable_fingerprint, root / row.rel_path)
        if fingerprint is not None:  # still being written (locked) -> try again next scan
            row.quick_fp = fingerprint
            row.state = "stable"
            report.became_stable += 1


async def _group_batch(
    session: AsyncSession,
    batch: Batch,
    batch_rows: list[InputFile],
    now: datetime,
    queue_config: queue.QueueConfig,
    stages: Collection[Stage | str],
) -> tuple[int, int]:
    """Create assets for ready groups of one batch; returns ``(assets, attached files)``."""
    by_path = {r.rel_path: r for r in batch_rows}
    result = group_files(by_path, batch_root=batch_root_of(batch.input_path))
    created = attached = 0
    for group in result.groups:
        member_rows = [by_path[m.rel_path] for m in group.members]
        if not _group_ready(group, member_rows, by_path):
            continue
        asset_ids = {r.asset_id for r in member_rows if r.asset_id is not None}
        if len(asset_ids) > 1:
            logger.warning("group spans several assets", extra={"primary": group.primary.rel_path})
            continue
        if asset_ids:
            asset = await session.get_one(Asset, next(iter(asset_ids)))
            attached += await _attach_files(session, asset, group, by_path)
            continue
        await _create_asset(session, batch, group, by_path, queue_config, stages)
        created += 1
    return created, attached


def _group_ready(
    group: AssetGroup, member_rows: list[InputFile], by_path: dict[str, InputFile]
) -> bool:
    """A group is ready when all its files (and its whole card structure) are stable."""
    if any(r.state not in READY_STATES for r in member_rows):
        return False
    root = group.card_structure_root
    if root is None:
        return True
    prefix = root + "/"
    return all(r.state in READY_STATES for p, r in by_path.items() if p.startswith(prefix))


async def _create_asset(
    session: AsyncSession,
    batch: Batch,
    group: AssetGroup,
    by_path: dict[str, InputFile],
    queue_config: queue.QueueConfig,
    stages: Collection[Stage | str],
) -> Asset:
    asset = Asset(
        id=uuid7(),
        batch_id=batch.id,
        media_type=group.media_type.value,
        status="ingesting",
        size_total=0,
    )
    session.add(asset)
    for member in group.members:
        row = by_path[member.rel_path]
        file = _asset_file(asset, member.role.value, row, group.card_structure_root)
        session.add(file)
        row.state = "grouped"
        row.asset_id = asset.id
        asset.size_total += row.size
        if member is group.primary:
            asset.primary_file_id = file.id
    await session.flush()
    await queue.create_jobs_for_asset(session, asset, batch, stages, queue_config)
    return asset


async def _attach_files(
    session: AsyncSession, asset: Asset, group: AssetGroup, by_path: dict[str, InputFile]
) -> int:
    """Attach late-arriving files (for example a sidecar) to an existing asset."""
    count = 0
    for member in group.members:
        row = by_path[member.rel_path]
        if row.asset_id is not None:
            continue
        session.add(_asset_file(asset, member.role.value, row, group.card_structure_root))
        row.state = "grouped"
        row.asset_id = asset.id
        asset.size_total += row.size
        count += 1
    return count


def _asset_file(asset: Asset, role: str, row: InputFile, card_root: str | None) -> AssetFile:
    return AssetFile(
        id=uuid7(),
        asset_id=asset.id,
        role=role,
        rel_path=row.rel_path,
        original_rel_path=row.rel_path,
        size=row.size,
        mtime=row.mtime,
        quick_fp=row.quick_fp,
        card_structure_root=card_root,
    )
