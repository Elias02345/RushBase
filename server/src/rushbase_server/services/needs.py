"""Needs lists (spec section 11): lists, items, candidates and passive auto-matching.

Rushbase never notifies anybody: matching only attaches candidates that a person (or Claude, on
request) reviews later.
"""

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core import uuid7
from rushbase_server.models import Asset, NeedCandidate, NeedItem, NeedList, Shot
from rushbase_server.search import hybrid
from rushbase_server.search.encoder import QueryEncoder
from rushbase_server.services import audit
from rushbase_server.services.catalog import AssetFilters

MATCH_DEPTH = 50
FILTER_KEYS = (
    "media_type",
    "camera_model",
    "lens_model",
    "log_profile",
    "orientation",
    "shot_size",
    "camera_angle",
    "interior_exterior",
    "time_of_day",
    "review_state",
    "consent",
)


class NeedsError(Exception):
    """A needs operation is invalid; ``status`` is the suggested HTTP status."""

    def __init__(self, message: str, status: int = 404) -> None:
        super().__init__(message)
        self.status = status


def clean_filters(data: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep only known, non-empty filter keys in a JSON-safe form."""
    result: dict[str, Any] = {}
    for key, value in (data or {}).items():
        if value in (None, "", []):
            continue
        if key in FILTER_KEYS:
            result[key] = str(value)
        elif key in {"date_from", "date_to"}:
            result[key] = value.isoformat() if isinstance(value, date) else str(value)
        elif key == "tags":
            result[key] = [str(t) for t in value][:20]
    return result


def to_asset_filters(
    data: Mapping[str, Any] | None, q: str | None = None, status: str | None = None
) -> AssetFilters:
    """Build :class:`AssetFilters` from a stored/validated filter mapping."""
    values = clean_filters(data)
    kwargs: dict[str, Any] = {k: values[k] for k in FILTER_KEYS if k in values}
    for key in ("date_from", "date_to"):
        if key in values:
            kwargs[key] = date.fromisoformat(values[key])
    return AssetFilters(q=q, status=status, tags=tuple(values.get("tags", ())), **kwargs)


# --- reading ---------------------------------------------------------------------------------


async def load_list(session: AsyncSession, need_id: UUID) -> NeedList:
    """The list or :class:`NeedsError`."""
    need = await session.get(NeedList, need_id)
    if need is None:
        raise NeedsError("needs list not found")
    return need


async def load_item(session: AsyncSession, item_id: UUID) -> NeedItem:
    """The item or :class:`NeedsError`."""
    item = await session.get(NeedItem, item_id)
    if item is None:
        raise NeedsError("needs item not found")
    return item


async def list_summaries(
    session: AsyncSession, status: str | None, offset: int, limit: int
) -> tuple[list[dict[str, Any]], bool]:
    """Lists (newest first) with item and candidate counts; second value: more pages exist."""
    stmt = select(NeedList).order_by(NeedList.created_at.desc(), NeedList.id.desc())
    if status:
        stmt = stmt.where(NeedList.status == status)
    rows = (await session.execute(stmt.offset(offset).limit(limit + 1))).scalars().all()
    page = rows[:limit]
    result = []
    for need in page:
        items = (
            await session.execute(
                select(NeedItem.status, func.count())
                .where(NeedItem.list_id == need.id)
                .group_by(NeedItem.status)
            )
        ).all()
        candidates = await session.scalar(
            select(func.count())
            .select_from(NeedCandidate)
            .join(NeedItem, NeedItem.id == NeedCandidate.item_id)
            .where(NeedItem.list_id == need.id, NeedCandidate.status != "rejected")
        )
        result.append(
            {
                "id": str(need.id),
                "title": need.title,
                "purpose": need.purpose,
                "status": need.status,
                "created_at": need.created_at.isoformat(),
                "items": {str(row[0]): int(row[1]) for row in items},
                "candidates": int(candidates or 0),
            }
        )
    return result, len(rows) > limit


async def detail(session: AsyncSession, need_id: UUID) -> dict[str, Any]:
    """One list with its items and all candidates (asset ids; callers add presentation)."""
    need = await load_list(session, need_id)
    items = (
        (
            await session.execute(
                select(NeedItem)
                .where(NeedItem.list_id == need_id)
                .order_by(NeedItem.position, NeedItem.id)
            )
        )
        .scalars()
        .all()
    )
    candidates = (
        (
            await session.execute(
                select(NeedCandidate)
                .where(NeedCandidate.item_id.in_([i.id for i in items]))
                .order_by(NeedCandidate.score.desc(), NeedCandidate.id)
            )
        )
        .scalars()
        .all()
        if items
        else []
    )
    shot_idx: dict[UUID, int] = {}
    shot_ids = {c.shot_id for c in candidates if c.shot_id}
    if shot_ids:
        rows = await session.execute(select(Shot.id, Shot.idx).where(Shot.id.in_(shot_ids)))
        shot_idx = {sid: int(idx) for sid, idx in rows.all()}
    return {
        "id": str(need.id),
        "title": need.title,
        "purpose": need.purpose,
        "status": need.status,
        "created_by": need.created_by_kind,
        "created_at": need.created_at.isoformat(),
        "items": [
            {
                "id": str(item.id),
                "description": item.description,
                "filters": item.filters,
                "count_wanted": item.count_wanted,
                "status": item.status,
                "confirmed": sum(
                    1 for c in candidates if c.item_id == item.id and c.status == "confirmed"
                ),
                "candidates": [
                    {
                        "id": str(c.id),
                        "asset_id": str(c.asset_id),
                        "shot_idx": shot_idx.get(c.shot_id) if c.shot_id else None,
                        "score": round(c.score, 4),
                        "status": c.status,
                        "source": c.source,
                    }
                    for c in candidates
                    if c.item_id == item.id
                ],
            }
            for item in items
        ],
    }


# --- writing ---------------------------------------------------------------------------------


async def create_list(
    session: AsyncSession,
    title: str,
    purpose: str,
    items: Sequence[tuple[str, Mapping[str, Any] | None, int]],
    creator_kind: str,
    creator_id: UUID | None,
) -> NeedList:
    """Create a list with its items."""
    need = NeedList(
        id=uuid7(),
        title=title.strip(),
        purpose=purpose.strip(),
        created_by_kind=creator_kind,
        created_by_id=creator_id,
    )
    session.add(need)
    await session.flush()
    await add_items(session, need, items)
    audit.record(
        session,
        actor_kind="mcp" if creator_kind == "mcp" else "user",
        actor_id=creator_id,
        action="needs.create",
        target_kind="need_list",
        target_id=need.id,
        details={"title": need.title, "items": len(items)},
    )
    return need


async def add_items(
    session: AsyncSession,
    need: NeedList,
    items: Sequence[tuple[str, Mapping[str, Any] | None, int]],
) -> list[NeedItem]:
    """Append items to a list."""
    start = int(
        await session.scalar(
            select(func.coalesce(func.max(NeedItem.position), -1)).where(
                NeedItem.list_id == need.id
            )
        )
        or 0
    )
    created = []
    for offset, (description, filters, count) in enumerate(items, start=1):
        item = NeedItem(
            id=uuid7(),
            list_id=need.id,
            position=start + offset,
            description=description.strip(),
            filters=clean_filters(filters),
            count_wanted=count,
        )
        session.add(item)
        created.append(item)
    await session.flush()
    return created


async def set_candidate(
    session: AsyncSession,
    item: NeedItem,
    asset_id: UUID,
    shot_idx: int | None,
    status: str,
    source: str,
    score: float = 0.0,
) -> NeedCandidate:
    """Create or update the candidate of ``asset_id`` for ``item``."""
    if await session.get(Asset, asset_id) is None:
        raise NeedsError("asset not found")
    shot_id = None
    if shot_idx is not None:
        shot_id = await session.scalar(
            select(Shot.id).where(Shot.asset_id == asset_id, Shot.idx == shot_idx)
        )
        if shot_id is None:
            raise NeedsError("shot not found")
    row = (
        await session.execute(
            select(NeedCandidate).where(
                NeedCandidate.item_id == item.id, NeedCandidate.asset_id == asset_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = NeedCandidate(
            id=uuid7(),
            item_id=item.id,
            asset_id=asset_id,
            shot_id=shot_id,
            score=score,
            status=status,
            source=source,
        )
        session.add(row)
    else:
        row.status = status
        if source != "auto":
            row.source = source
        if shot_id is not None:
            row.shot_id = shot_id
    await session.flush()
    if status == "confirmed":
        await refresh_fulfilment(session, item)
    return row


async def refresh_fulfilment(session: AsyncSession, item: NeedItem) -> None:
    """Mark an item fulfilled once enough candidates are confirmed (never reopens a drop)."""
    confirmed = int(
        await session.scalar(
            select(func.count())
            .select_from(NeedCandidate)
            .where(NeedCandidate.item_id == item.id, NeedCandidate.status == "confirmed")
        )
        or 0
    )
    if item.status == "open" and confirmed >= item.count_wanted:
        item.status = "fulfilled"


# --- auto-matching ---------------------------------------------------------------------------


async def match_asset(
    session: AsyncSession, encoder: QueryEncoder, asset_id: UUID, threshold: float
) -> int:
    """Attach ``asset_id`` as candidate to open items it matches; returns how many were added.

    The item description is searched (hybrid) inside the item's filters; the asset must rank in
    the top ``MATCH_DEPTH`` with a fused score of at least ``threshold``. Existing candidates
    (including rejected ones) are left alone.
    """
    asset = await session.get(Asset, asset_id)
    if asset is None or asset.status != "filed":
        return 0
    items = (
        (
            await session.execute(
                select(NeedItem)
                .join(NeedList, NeedList.id == NeedItem.list_id)
                .where(NeedList.status == "open", NeedItem.status == "open")
            )
        )
        .scalars()
        .all()
    )
    added = 0
    for item in items:
        known = await session.scalar(
            select(NeedCandidate.id).where(
                NeedCandidate.item_id == item.id, NeedCandidate.asset_id == asset_id
            )
        )
        if known is not None:
            continue
        filters = to_asset_filters(item.filters)
        result = await hybrid.search(
            session, encoder, item.description, "asset", filters, 0, MATCH_DEPTH
        )
        score = next((h.score for h in result.hits if h.key[0] == asset_id), None)
        if score is not None and score >= threshold:
            session.add(
                NeedCandidate(
                    id=uuid7(),
                    item_id=item.id,
                    asset_id=asset_id,
                    score=score,
                    status="candidate",
                    source="auto",
                )
            )
            added += 1
    await session.flush()
    return added
