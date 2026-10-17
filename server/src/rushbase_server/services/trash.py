"""Delete to trash: admin-initiated, reversible until the retention period has passed.

Trashing moves the files of an asset (or its whole camera card directory when no other asset
lives in it), its JSON sidecar, the XMP written by Rushbase and the optional central sidecar
copy to ``.rushbase/trash/<asset-id>/`` keeping the library-relative structure, using the
rename-only, never-overwrite helper of filing. ``manifest.json`` records every move. Restoring
moves everything back when the original locations are free.

:func:`purge_expired` is the **only** code path that deletes media from the library. It removes
nothing but the trash folder of an asset whose retention period has passed (and its derived
previews), audits every purge and never touches ``Input/`` or ``Content/``.
"""

import asyncio
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core import SYSTEM_DIR, resolve_in_root
from rushbase_core.fsops import append_line, atomic_write_bytes, lexists
from rushbase_core.layout import DERIVED_DIR, JOURNAL_DIR, META_DIR, TRASH_DIR
from rushbase_core.sidecar import RUSHBASE_XMP_MARKER, XMP_EXTENSION, sidecar_rel_path
from rushbase_server.db import SessionFactory
from rushbase_server.models import Asset, AssetFile, Job, MoveJournal, TrashItem
from rushbase_server.services import audit
from rushbase_server.services.filing import card_siblings
from rushbase_server.services.quarantine import (
    QuarantineMove,
    execute_moves,
    prune_empty,
)
from rushbase_server.services.settings_store import (
    DEFAULT_TRASH_RETENTION_DAYS,
    TRASH_RETENTION_KEY,
    get_setting,
)

logger = logging.getLogger(__name__)

MANIFEST_FILE = "manifest.json"
TRASHABLE_STATUSES = ("filed", "duplicate")
OPEN_JOB_STATES = ("blocked", "queued", "leased")


class TrashError(Exception):
    """A trash operation cannot be carried out; ``conflicts`` lists occupied locations."""

    def __init__(self, message: str, conflicts: list[str] | None = None) -> None:
        super().__init__(message)
        self.conflicts = conflicts or []


@dataclass(frozen=True)
class TrashSummary:
    """A trashed asset as shown on the trash page."""

    asset_id: UUID
    filename: str
    media_type: str
    size_total: int
    prior_status: str
    trashed_at: datetime
    expires_at: datetime
    files: int


def trash_dir(asset_id: UUID) -> str:
    """Library-relative trash folder of an asset."""
    return f"{SYSTEM_DIR}/{TRASH_DIR}/{asset_id}"


async def retention_days(session: AsyncSession) -> int:
    """The configured retention period in days."""
    value = await get_setting(session, TRASH_RETENTION_KEY, DEFAULT_TRASH_RETENTION_DAYS)
    return value if isinstance(value, int) and value >= 1 else DEFAULT_TRASH_RETENTION_DAYS


def _json_owner(path: Path) -> str | None:
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("asset_id"))
    except (OSError, ValueError, AttributeError):
        return None


def _is_rushbase_xmp(path: Path) -> bool:
    try:
        return RUSHBASE_XMP_MARKER in path.read_bytes()
    except OSError:
        return False


def sidecar_files(root: Path, asset: Asset, files: list[AssetFile]) -> list[str]:
    """Sidecar files Rushbase wrote for ``asset`` (JSON, own XMP, central copy) that exist."""
    found: list[str] = []
    primary = next((f for f in files if f.id == asset.primary_file_id), files[0])
    name = PurePosixPath(primary.rel_path)
    if asset.content_rel_dir:
        for filename in (name.name, f"{name.name}.{str(asset.id)[:8]}"):
            rel = sidecar_rel_path(asset.content_rel_dir, filename)
            if _json_owner(resolve_in_root(root, rel)) == str(asset.id):
                found.append(rel)
                break
    if asset.media_type == "photo":
        for suffix in (XMP_EXTENSION, ".rushbase" + XMP_EXTENSION):
            rel = f"{name.parent}/{name.stem}{suffix}"
            if _is_rushbase_xmp(resolve_in_root(root, rel)):
                found.append(rel)
                break
    central = f"{SYSTEM_DIR}/{META_DIR}/{asset.id}.json"
    if lexists(resolve_in_root(root, central)):
        found.append(central)
    return found


async def _plan(
    session: AsyncSession, root: Path, asset: Asset, files: list[AssetFile]
) -> list[QuarantineMove]:
    target = trash_dir(asset.id)
    card = next((f.card_structure_root for f in files if f.card_structure_root), None)
    moves: list[QuarantineMove] = []
    if card is not None and all(s.id == asset.id for s in await card_siblings(session, card)):
        moves.append(QuarantineMove("dir", card, f"{target}/{card}"))
    else:
        moves.extend(QuarantineMove("file", f.rel_path, f"{target}/{f.rel_path}") for f in files)
    extras = await asyncio.to_thread(sidecar_files, root, asset, files)
    dirs = [m.src for m in moves if m.kind == "dir"]
    moves.extend(
        QuarantineMove("file", rel, f"{target}/{rel}")
        for rel in extras
        if not any(rel.startswith(d + "/") for d in dirs)
    )
    return moves


def journal_moves(
    root: Path, now: datetime, asset_id: UUID, moves: list[QuarantineMove], why: str
) -> None:
    path = root / SYSTEM_DIR / JOURNAL_DIR / f"{now:%Y-%m}.jsonl"
    for move in moves:
        line = json.dumps(
            {
                "at": now.isoformat(),
                "kind": move.kind,
                "asset_id": str(asset_id),
                "from": move.src,
                "to": move.dst,
                "reason": why,
            },
            ensure_ascii=False,
        )
        append_line(path, line)


async def trash_asset(
    session: AsyncSession, root: Path, asset_id: UUID, user_id: UUID, now: datetime
) -> TrashItem:
    """Move a filed or duplicate asset into the trash (the caller commits).

    Raises:
        TrashError: unknown asset, wrong status, a file is missing or in use.
    """
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise TrashError("asset not found")
    if asset.status not in TRASHABLE_STATUSES:
        raise TrashError("only filed assets and duplicates can be moved to the trash")
    files = list(
        (await session.execute(select(AssetFile).where(AssetFile.asset_id == asset.id)))
        .scalars()
        .all()
    )
    if not files:
        raise TrashError("asset has no files")
    planned = await _plan(session, root, asset, files)
    try:
        done, missing = await asyncio.to_thread(execute_moves, root, planned)
    except OSError as exc:
        raise TrashError(f"could not move the files: {exc}") from exc
    if not done:
        raise TrashError("none of the files exist any more")
    manifest = {
        "asset_id": str(asset.id),
        "at": now.isoformat(),
        "prior_status": asset.status,
        "moves": [vars(m) for m in done],
        "missing": missing,
        "files": {
            str(f.id): {"rel_path": f.rel_path, "card_structure_root": f.card_structure_root}
            for f in files
        },
    }
    try:
        await asyncio.to_thread(
            atomic_write_bytes,
            resolve_in_root(root, f"{trash_dir(asset.id)}/{MANIFEST_FILE}"),
            (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )
        await asyncio.to_thread(journal_moves, root, now, asset.id, done, "trash")
    except OSError as exc:
        back = [QuarantineMove(m.kind, m.dst, m.src) for m in reversed(done)]
        await asyncio.to_thread(execute_moves, root, back)
        raise TrashError(f"could not write the trash manifest: {exc}") from exc
    for file in files:
        for move in done:
            if move.kind == "dir" and file.rel_path.startswith(move.src + "/"):
                file.rel_path = move.dst + file.rel_path[len(move.src) :]
                file.card_structure_root = move.dst
            elif move.kind == "file" and file.rel_path == move.src:
                file.rel_path = move.dst
    item = TrashItem(
        asset_id=asset.id,
        trashed_at=now,
        trashed_by=user_id,
        prior_status=asset.status,
        manifest=manifest,
    )
    session.add(item)
    asset.status = "trashed"
    await session.execute(
        update(Job)
        .where(Job.asset_id == asset.id, Job.state.in_(OPEN_JOB_STATES))
        .values(state="cancelled", lease_owner=None, lease_expires_at=None)
    )
    for move in done:
        session.add(
            MoveJournal(
                asset_id=asset.id, from_rel=move.src, to_rel=move.dst, at=now, reason="trash"
            )
        )
    audit.record(
        session,
        actor_kind="user",
        actor_id=user_id,
        action="asset.trash",
        target_kind="asset",
        target_id=asset.id,
        details={"prior_status": item.prior_status, "files": len(files), "size": asset.size_total},
    )
    await session.flush()
    return item


async def restore_asset(
    session: AsyncSession, root: Path, asset_id: UUID, user_id: UUID, now: datetime
) -> Asset:
    """Move a trashed asset back to where it came from (the caller commits).

    Raises:
        TrashError: not trashed, an item is gone from the trash, or an original location is
            occupied (``conflicts`` names them; nothing is moved in that case).
    """
    item = await session.get(TrashItem, asset_id)
    asset = await session.get(Asset, asset_id)
    if item is None or asset is None or asset.status != "trashed":
        raise TrashError("asset is not in the trash")
    moves = [QuarantineMove(**m) for m in item.manifest.get("moves", [])]
    occupied = [m.src for m in moves if lexists(resolve_in_root(root, m.src))]
    if occupied:
        raise TrashError("original location is occupied", occupied)
    gone = [m.dst for m in moves if not lexists(resolve_in_root(root, m.dst))]
    if gone:
        raise TrashError(f"trashed item is missing: {gone[0]}")
    back = [QuarantineMove(m.kind, m.dst, m.src) for m in moves]
    try:
        await asyncio.to_thread(execute_moves, root, back)
        await asyncio.to_thread(journal_moves, root, now, asset.id, back, "trash_restore")
    except OSError as exc:
        raise TrashError(f"could not move the files back: {exc}") from exc
    before = item.manifest.get("files", {})
    rows = await session.execute(select(AssetFile).where(AssetFile.asset_id == asset.id))
    for file in rows.scalars().all():
        previous = before.get(str(file.id))
        if previous is not None:
            file.rel_path = previous["rel_path"]
            file.card_structure_root = previous["card_structure_root"]
    asset.status = item.prior_status
    for move in back:
        session.add(
            MoveJournal(
                asset_id=asset.id,
                from_rel=move.src,
                to_rel=move.dst,
                at=now,
                reason="trash_restore",
            )
        )
    audit.record(
        session,
        actor_kind="user",
        actor_id=user_id,
        action="asset.trash_restore",
        target_kind="asset",
        target_id=asset.id,
        details={"status": item.prior_status},
    )
    await session.delete(item)
    await session.flush()
    await asyncio.to_thread(_clear_trash_dir, root, trash_dir(asset.id))
    return asset


def _clear_trash_dir(root: Path, rel_dir: str) -> None:
    """Remove the manifest and the (now empty) trash folder of a restored asset."""
    resolve_in_root(root, f"{rel_dir}/{MANIFEST_FILE}").unlink(missing_ok=True)
    prune_empty(root, rel_dir)


async def list_trash(session: AsyncSession) -> list[TrashSummary]:
    """Trashed assets, oldest first (the next to expire on top)."""
    days = await retention_days(session)
    rows = await session.execute(
        select(TrashItem, Asset, AssetFile.original_rel_path)
        .join(Asset, Asset.id == TrashItem.asset_id)
        .outerjoin(AssetFile, AssetFile.id == Asset.primary_file_id)
        .order_by(TrashItem.trashed_at)
    )
    return [
        TrashSummary(
            asset_id=asset.id,
            filename=(path or "").rsplit("/", 1)[-1],
            media_type=asset.media_type,
            size_total=asset.size_total,
            prior_status=item.prior_status,
            trashed_at=item.trashed_at,
            expires_at=item.trashed_at + timedelta(days=days),
            files=len(item.manifest.get("files", {})),
        )
        for item, asset, path in rows.all()
    ]


# --- permanent deletion (the only code path that removes media) --------------------------------


def _remove_tree(root: Path, rel_dir: str) -> int:
    """Delete the files below ``rel_dir`` and its directories; returns the number of files.

    Only folders below ``.rushbase/trash/`` or ``.rushbase/derived/`` are accepted; symbolic
    links are removed, never followed.
    """
    allowed = (f"{SYSTEM_DIR}/{TRASH_DIR}/", f"{SYSTEM_DIR}/{DERIVED_DIR}/")
    if not rel_dir.startswith(allowed) or ".." in rel_dir.split("/"):
        raise ValueError(f"refusing to delete outside the trash: {rel_dir}")
    top = resolve_in_root(root, rel_dir)
    # A share user could replace `.rushbase`, `trash` or the asset folder with a symlink to
    # redirect the deletion: refuse unless every component below the root is a real directory.
    current = root
    for part in Path(rel_dir).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"refusing to delete through a symbolic link: {current}")
    if not top.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"refusing to delete outside the library: {rel_dir}")
    removed = 0
    for directory, subdirs, names in os.walk(top, topdown=False, followlinks=False):
        for name in names:
            Path(directory, name).unlink()
            removed += 1
        for name in subdirs:
            link = Path(directory, name)
            if link.is_symlink():
                link.unlink()
            else:
                link.rmdir()
    if top.exists():
        top.rmdir()
    return removed


async def purge_expired(factory: SessionFactory, root: Path, now_fn: Callable[[], datetime]) -> int:
    """Permanently delete every trashed asset whose retention period has passed.

    Returns the number of purged assets. A failure leaves the asset in the trash for the next
    pass. Each purge is audited and appended to the move journal.
    """
    async with factory() as session:
        cutoff = now_fn() - timedelta(days=await retention_days(session))
        expired = list(
            (await session.execute(select(TrashItem.asset_id).where(TrashItem.trashed_at < cutoff)))
            .scalars()
            .all()
        )
    purged = 0
    for asset_id in expired:
        async with factory() as session:
            asset = await session.get(Asset, asset_id)
            item = await session.get(TrashItem, asset_id)
            if asset is None or item is None or asset.status != "trashed":
                continue
            try:
                removed = await asyncio.to_thread(_remove_tree, root, trash_dir(asset_id))
                derived = f"{SYSTEM_DIR}/{DERIVED_DIR}/{asset_id}"
                await asyncio.to_thread(_remove_tree, root, derived)
            except OSError:
                logger.exception("purging the trash failed", extra={"asset": str(asset_id)})
                continue
            moves = [QuarantineMove("dir", trash_dir(asset_id), "")]
            await asyncio.to_thread(journal_moves, root, now_fn(), asset_id, moves, "purge")
            audit.record(
                session,
                actor_kind="system",
                action="asset.purge",
                target_kind="asset",
                target_id=asset_id,
                details={
                    "files": removed,
                    "size": asset.size_total,
                    "trashed_at": item.trashed_at.isoformat(),
                    "moves": [m["src"] for m in item.manifest.get("moves", [])],
                },
            )
            await session.execute(
                update(Asset).where(Asset.duplicate_of == asset_id).values(duplicate_of=None)
            )
            await session.delete(asset)
            await session.commit()
            purged += 1
    return purged
