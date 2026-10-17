"""Content migration: re-file existing assets with a new filing template (disabled by default).

This is the one place where filed material moves again (invariant 3 otherwise forbids it), so it
is an explicit administrator tool: preview first, then execute with a typed confirmation. Moves
use the rename-only filing helpers, every move is journalled, sidecars follow their asset and a
relink list (old path to new path per operating system) is produced for DaVinci Resolve. It is
never reachable through MCP.
"""

import asyncio
import csv
import io
import json
import logging
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core import SYSTEM_DIR, resolve_in_root
from rushbase_core.fsops import append_line, atomic_write_bytes, safe_rename
from rushbase_core.layout import JOURNAL_DIR
from rushbase_core.sidecar import sidecar_rel_path
from rushbase_server.models import Asset, AssetFile, Batch, MoveJournal
from rushbase_server.services import audit, catalog, filing_rules, placement, trash

logger = logging.getLogger(__name__)

MAX_ASSETS = 500
CONFIRM_WORD = "MIGRATE"


class MigrationError(Exception):
    """The migration request is invalid or cannot be carried out."""


@dataclass
class UnitPlan:
    """One unit (an asset, or all assets of a camera card directory) and where it would go."""

    assets: list[Asset]
    files: list[AssetFile]
    old_dir: str
    new_dir: str
    spec: placement.UnitSpec
    warnings: list[str] = field(default_factory=list)
    skipped: str | None = None


@dataclass(frozen=True)
class MigrationItem:
    """Preview row of a unit."""

    asset_ids: tuple[UUID, ...]
    filename: str
    old_dir: str
    new_dir: str
    warnings: tuple[str, ...]
    skipped: str | None


def expected_confirmation(count: int) -> str:
    """The text an administrator has to type for a run that moves ``count`` assets."""
    return f"{CONFIRM_WORD} {count}"


async def plan_units(
    session: AsyncSession,
    root: Path | None,
    asset_ids: list[UUID],
    template: str,
    fallbacks: Mapping[str, str],
    budget: int,
) -> list[UnitPlan]:
    """Evaluate ``template`` for the selected filed assets; touches nothing."""
    parsed = filing_rules.parse_rule_template(template)
    cleaned = filing_rules.clean_fallbacks(fallbacks)
    assets = list(
        (
            await session.execute(
                select(Asset).where(Asset.id.in_(asset_ids), Asset.status == "filed")
            )
        )
        .scalars()
        .all()
    )
    files: dict[UUID, list[AssetFile]] = defaultdict(list)
    for row in (
        (await session.execute(select(AssetFile).where(AssetFile.asset_id.in_(asset_ids))))
        .scalars()
        .all()
    ):
        files[row.asset_id].append(row)
    groups: dict[str, list[Asset]] = {}
    for asset in assets:
        card = next((f.card_structure_root for f in files[asset.id] if f.card_structure_root), None)
        groups.setdefault(card or f"asset:{asset.id}", []).append(asset)
    plans = []
    for key, members in groups.items():
        card = None if key.startswith("asset:") else key
        unit_files = [f for a in members for f in files[a.id]]
        spec = placement.UnitSpec(
            moves=[placement.MoveSpec(str(f.id), f.rel_path) for f in unit_files],
            card_root=card,
            whole_directory=card is not None,
        )
        anchor = members[0]
        batch = await session.get(Batch, anchor.batch_id) if anchor.batch_id else None
        plan = placement.plan_directory(
            root,
            parsed,
            filing_rules.context_for(anchor, batch),
            cleaned,
            placement.tail_length_for(spec),
            budget,
        )
        old = anchor.content_rel_dir or ""
        current_parent = str(PurePosixPath(old).parent) if card else old
        unit = UnitPlan(members, unit_files, old, plan.path, spec, list(plan.warnings))
        if not anchor.content_rel_dir:
            unit.skipped = "asset has no content directory"
        elif current_parent == plan.path:
            unit.skipped = "already in the target folder"
        elif card is not None and await _card_shared(session, card, {a.id for a in members}):
            unit.skipped = "the card folder also holds assets that are not selected"
        plans.append(unit)
    return plans


async def _card_shared(session: AsyncSession, card: str, selected: set[UUID]) -> bool:
    owners = set(
        (
            await session.execute(
                select(AssetFile.asset_id).where(AssetFile.card_structure_root == card)
            )
        )
        .scalars()
        .all()
    )
    return not owners <= selected


def preview_items(plans: list[UnitPlan]) -> list[MigrationItem]:
    """Preview rows for the web UI."""
    return [
        MigrationItem(
            asset_ids=tuple(a.id for a in p.assets),
            filename=PurePosixPath(p.files[0].rel_path).name if p.files else "",
            old_dir=p.old_dir,
            new_dir=p.new_dir,
            warnings=tuple(p.warnings),
            skipped=p.skipped,
        )
        for p in plans
    ]


def _move_sidecars(root: Path, old: list[str], new_dir: str, new_primary: str) -> None:
    """Rename the old JSON/XMP sidecars next to the moved files (best effort, never overwrites)."""
    name = PurePosixPath(new_primary)
    for rel in old:
        path = PurePosixPath(rel)
        if path.suffix == ".json":
            target = sidecar_rel_path(new_dir, name.name)
        else:
            suffix = ".rushbase.xmp" if path.name.endswith(".rushbase.xmp") else ".xmp"
            target = f"{name.parent}/{name.stem}{suffix}"
        try:
            safe_rename(resolve_in_root(root, rel), resolve_in_root(root, target))
        except OSError:
            logger.warning("sidecar not moved", extra={"from": rel, "to": target})


async def _execute_unit(
    session: AsyncSession, root: Path, unit: UnitPlan, now: datetime, user_id: UUID
) -> list[tuple[str, str, UUID]]:
    """Move one unit and update the database; returns ``(old_rel, new_rel, asset_id)`` rows."""
    owner = {str(f.id): f for f in unit.files}
    sidecar_sources = {
        a.id: await asyncio.to_thread(
            trash.sidecar_files, root, a, [f for f in unit.files if f.asset_id == a.id]
        )
        for a in unit.assets
    }
    result = await asyncio.to_thread(placement.place_unit, root, unit.spec, unit.new_dir)
    rows: list[tuple[str, str, UUID]] = []
    try:
        for file_id, new_path in result.new_paths.items():
            file = owner[file_id]
            rows.append((file.rel_path, new_path, file.asset_id))
            file.rel_path = new_path
            if result.card_root is not None:
                file.card_structure_root = result.card_root
        for asset in unit.assets:
            asset.content_rel_dir = result.card_root or result.directory
            primary = next(f for f in unit.files if f.id == asset.primary_file_id)
            if result.card_root is None:
                local = [s for s in sidecar_sources[asset.id] if not s.startswith(SYSTEM_DIR)]
                await asyncio.to_thread(
                    _move_sidecars, root, local, asset.content_rel_dir, primary.rel_path
                )
        for old, new, asset_id in rows:
            session.add(
                MoveJournal(asset_id=asset_id, from_rel=old, to_rel=new, at=now, reason="migration")
            )
        for asset in unit.assets:
            audit.record(
                session,
                actor_kind="user",
                actor_id=user_id,
                action="asset.migrated",
                target_kind="asset",
                target_id=asset.id,
                details={"from": unit.old_dir, "to": asset.content_rel_dir},
            )
        journal = root / SYSTEM_DIR / JOURNAL_DIR / f"{now:%Y-%m}.jsonl"
        for old, new, asset_id in rows:
            line = json.dumps(
                {
                    "at": now.isoformat(),
                    "kind": "file",
                    "asset_id": str(asset_id),
                    "from": old,
                    "to": new,
                    "reason": "migration",
                },
                ensure_ascii=False,
            )
            await asyncio.to_thread(append_line, journal, line)
        await session.commit()
    except BaseException:
        await session.rollback()
        await asyncio.to_thread(placement.rollback, root, result)
        raise
    return rows


def relink_csv(rows: list[tuple[str, str, UUID]], roots: Mapping[str, object]) -> str:
    """CSV with old and new paths (library-relative and per configured operating system)."""
    platforms = [p for p in ("windows", "macos", "linux") if roots.get(p)]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        ["asset_id", "old_rel", "new_rel"]
        + [f"{kind}_{p}" for p in platforms for kind in ("old", "new")]
    )
    for old, new, asset_id in rows:
        old_abs, new_abs = catalog.platform_paths(roots, old), catalog.platform_paths(roots, new)
        writer.writerow(
            [str(asset_id), old, new] + [x[p] for p in platforms for x in (old_abs, new_abs)]
        )
    return out.getvalue()


async def execute(
    session: AsyncSession,
    root: Path,
    plans: list[UnitPlan],
    roots: Mapping[str, object],
    user_id: UUID,
    now: datetime,
) -> tuple[int, int, str, str]:
    """Move every non-skipped unit; returns ``(moved, failed, relink_csv, relink_rel_path)``.

    Each unit commits on its own, so a failure leaves earlier units migrated and consistent.
    """
    rows: list[tuple[str, str, UUID]] = []
    moved = failed = 0
    for unit in plans:
        if unit.skipped:
            continue
        try:
            rows.extend(await _execute_unit(session, root, unit, now, user_id))
            moved += len(unit.assets)
        except OSError:
            logger.exception("migration of a unit failed", extra={"dir": unit.old_dir})
            failed += len(unit.assets)
    text = relink_csv(rows, roots)
    rel = f"{SYSTEM_DIR}/{JOURNAL_DIR}/relink-{now:%Y%m%dT%H%M%S}.csv"
    await asyncio.to_thread(atomic_write_bytes, resolve_in_root(root, rel), text.encode("utf-8"))
    audit.record(
        session,
        actor_kind="user",
        actor_id=user_id,
        action="migration.execute",
        details={"moved": moved, "failed": failed, "relink": rel},
    )
    await session.commit()
    return moved, failed, text, rel
