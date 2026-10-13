"""Duplicate detection by content hash.

An asset is a duplicate when every one of its files has a BLAKE3 digest that occurs among the
files of one already **filed** asset. Duplicates are not filed into ``Content/``: they stay
untouched in ``Input/`` and are flagged (``status = 'duplicate'``, ``duplicate_of``); an admin
can trash them later. The space they occupy is reported as "wasted bytes" in the statistics.
"""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.models import Asset, AssetFile, Job
from rushbase_server.services import audit

OPEN_JOB_STATES = ("blocked", "queued", "leased")


async def find_original(session: AsyncSession, asset_id: UUID) -> UUID | None:
    """Return the id of the oldest filed asset that contains all hashes of ``asset_id``.

    Returns ``None`` when the asset has unhashed files (it cannot be judged yet) or no match.
    """
    digests = (
        (await session.execute(select(AssetFile.blake3).where(AssetFile.asset_id == asset_id)))
        .scalars()
        .all()
    )
    if not digests or any(d is None for d in digests):
        return None
    wanted = {d for d in digests if d is not None}
    matches = await session.execute(
        select(AssetFile.asset_id)
        .join(Asset, Asset.id == AssetFile.asset_id)
        .where(
            AssetFile.blake3.in_(wanted),
            AssetFile.asset_id != asset_id,
            Asset.status == "filed",
        )
        .group_by(AssetFile.asset_id)
        .having(func.count(func.distinct(AssetFile.blake3)) == len(wanted))
        .order_by(AssetFile.asset_id)
        .limit(1)
    )
    return matches.scalar_one_or_none()


async def mark_duplicate(session: AsyncSession, asset: Asset, original_id: UUID) -> None:
    """Flag ``asset`` as a duplicate of ``original_id`` and cancel its remaining jobs."""
    asset.status = "duplicate"
    asset.duplicate_of = original_id
    await session.execute(
        update(Job)
        .where(Job.asset_id == asset.id, Job.state.in_(OPEN_JOB_STATES))
        .values(state="cancelled", lease_owner=None, lease_expires_at=None)
    )
    audit.record(
        session,
        actor_kind="system",
        action="asset.duplicate",
        target_kind="asset",
        target_id=asset.id,
        details={"duplicate_of": str(original_id), "size": asset.size_total},
    )


async def detect(session: AsyncSession, asset: Asset) -> bool:
    """Mark ``asset`` as duplicate when it matches a filed asset; returns whether it did."""
    if asset.status in ("duplicate", "filed", "trashed", "quarantined"):
        return False
    original = await find_original(session, asset.id)
    if original is None:
        return False
    await mark_duplicate(session, asset, original)
    return True
