"""Golden labels: expected values of core and taxonomy fields (evaluation ground truth)."""

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.models import GoldenLabel
from rushbase_server.services.settings_store import get_setting

CORE_FIELDS = (
    "shot_size",
    "camera_angle",
    "interior_exterior",
    "time_of_day",
    "people_count",
    "person_identifiable",
)
FROM_CORRECTIONS_KEY = "golden.from_corrections"


async def corrections_enabled(session: AsyncSession) -> bool:
    """Whether human corrections of AI values are copied into the golden set (default: yes)."""
    return bool(await get_setting(session, FROM_CORRECTIONS_KEY, True))


async def upsert_label(
    session: AsyncSession,
    asset_id: UUID,
    shot_id: UUID | None,
    field: str,
    expected: str,
    labeller_id: UUID | None,
    origin: str = "label",
) -> None:
    """Store (or replace) the expected value of ``field`` for an asset or shot."""
    stmt = insert(GoldenLabel).values(
        asset_id=asset_id,
        shot_id=shot_id,
        field=field,
        expected=expected,
        labeller_id=labeller_id,
        origin=origin,
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["asset_id", "shot_id", "field"],
            index_where=None,
            set_={"expected": expected, "labeller_id": labeller_id, "origin": origin},
        )
    )


async def delete_label(
    session: AsyncSession, asset_id: UUID, shot_id: UUID | None, field: str
) -> int:
    """Remove a label; returns the number of removed rows."""
    stmt = delete(GoldenLabel).where(GoldenLabel.asset_id == asset_id, GoldenLabel.field == field)
    stmt = stmt.where(
        GoldenLabel.shot_id.is_(None) if shot_id is None else GoldenLabel.shot_id == shot_id
    )
    result = await session.execute(stmt)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def labels_for(session: AsyncSession, asset_id: UUID) -> list[GoldenLabel]:
    """All labels of an asset (asset level and shots)."""
    rows = await session.execute(select(GoldenLabel).where(GoldenLabel.asset_id == asset_id))
    return list(rows.scalars())
