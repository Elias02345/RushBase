"""Where an asset was used: "used in project" feedback from Resolve, by hand or via Claude.

The table is feedback, not a source of truth: a rebuild from sidecars starts it empty and the
next ``rushbase resolve sync-usage`` refills it. Nothing here touches originals.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.models import Asset, AssetUsage

MAX_REPORT_ENTRIES = 5000


@dataclass(frozen=True)
class UsageEntry:
    """One clip of a project: how often the asset appears on timelines (0 = in the pool only)."""

    asset_id: UUID
    uses: int


@dataclass(frozen=True)
class ReportResult:
    """Counts of a usage report."""

    recorded: int
    cleared: int
    unknown: int


async def report(
    session: AsyncSession,
    *,
    project_name: str,
    machine: str,
    entries: Sequence[UsageEntry],
    source: str,
    now: datetime,
) -> ReportResult:
    """Upsert usage rows for entries with ``uses > 0``; drop this source's rows for ``uses == 0``.

    Unknown asset ids are counted and ignored. ``first_seen`` stays; ``last_seen`` and
    ``machine`` follow the latest report.
    """
    ids = {e.asset_id for e in entries}
    known = set((await session.execute(select(Asset.id).where(Asset.id.in_(ids)))).scalars())
    used = sorted({e.asset_id for e in entries if e.uses > 0 and e.asset_id in known}, key=str)
    unused = [e.asset_id for e in entries if e.uses <= 0 and e.asset_id in known]
    if used:
        stmt = insert(AssetUsage).values(
            [
                {
                    "asset_id": asset_id,
                    "project_name": project_name,
                    "source": source,
                    "machine": machine,
                    "first_seen": now,
                    "last_seen": now,
                }
                for asset_id in used
            ]
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["asset_id", "project_name", "source"],
                set_={"last_seen": stmt.excluded.last_seen, "machine": stmt.excluded.machine},
            )
        )
    cleared = 0
    if unused:
        result = await session.execute(
            delete(AssetUsage).where(
                AssetUsage.asset_id.in_(unused),
                AssetUsage.project_name == project_name,
                AssetUsage.source == source,
            )
        )
        cleared = int(result.rowcount or 0)  # type: ignore[attr-defined]
    return ReportResult(len(used), cleared, len(ids - known))


async def usage_of(session: AsyncSession, asset_id: UUID) -> list[AssetUsage]:
    """Usage rows of one asset, newest first."""
    rows = await session.execute(
        select(AssetUsage)
        .where(AssetUsage.asset_id == asset_id)
        .order_by(AssetUsage.last_seen.desc(), AssetUsage.project_name)
    )
    return list(rows.scalars())


async def remove(session: AsyncSession, asset_id: UUID, project_name: str, source: str) -> bool:
    """Delete one usage row; ``True`` when it existed."""
    result = await session.execute(
        delete(AssetUsage).where(
            AssetUsage.asset_id == asset_id,
            AssetUsage.project_name == project_name,
            AssetUsage.source == source,
        )
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def project_counts(session: AsyncSession) -> list[tuple[str, int]]:
    """Project names with the number of distinct assets used in them."""
    rows = await session.execute(
        select(AssetUsage.project_name, func.count(func.distinct(AssetUsage.asset_id)))
        .group_by(AssetUsage.project_name)
        .order_by(AssetUsage.project_name)
    )
    return [(name, int(count)) for name, count in rows]
