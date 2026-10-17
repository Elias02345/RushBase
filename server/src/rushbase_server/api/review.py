"""Review queue, human edits (asset, shot, bulk), collections and transcripts."""

from dataclasses import replace
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.api.catalog import AssetSummary, FiltersDep, _summary
from rushbase_server.api.deps import AdminDep, DbDep, EditorDep, RuntimeDep, ViewerDep, client_ip
from rushbase_server.models import (
    Asset,
    AudioEvent,
    Collection,
    CollectionAsset,
    Shot,
    TranscriptSegment,
)
from rushbase_server.runtime import Runtime
from rushbase_server.services import ai_sidecar, audit, catalog, editing

router = APIRouter(prefix="/api/v1", tags=["review"])
MAX_BULK = 500


class TagIn(BaseModel):
    """A tag by field key and value."""

    field: str = Field(min_length=1, max_length=40)
    value: str = Field(min_length=1, max_length=100)


class AssetEditIn(BaseModel):
    """Human edit of an asset. Only the fields that are sent change."""

    title: str | None = Field(default=None, max_length=300)
    rating: int | None = Field(default=None, ge=0, le=5)
    locked: bool | None = None
    consent: Literal["yes", "no", "unknown"] | None = None
    consent_ref: str | None = Field(default=None, max_length=300)
    person_identifiable: Literal["yes", "no", "unknown"] | None = None
    summary_short: str | None = Field(default=None, max_length=500)
    summary_short_en: str | None = Field(default=None, max_length=500)
    summary_long_de: str | None = Field(default=None, max_length=5000)
    summary_long_en: str | None = Field(default=None, max_length=5000)
    shot_size: str | None = Field(default=None, max_length=40)
    camera_angle: str | None = Field(default=None, max_length=40)
    interior_exterior: str | None = Field(default=None, max_length=40)
    time_of_day: str | None = Field(default=None, max_length=40)
    people_count: int | None = Field(default=None, ge=0, le=1000)
    add_tags: list[TagIn] = Field(default_factory=list, max_length=50)
    remove_tags: list[TagIn] = Field(default_factory=list, max_length=50)
    review_done: bool = False


class ShotEditIn(BaseModel):
    """Human edit of a shot."""

    description_short_de: str | None = Field(default=None, max_length=500)
    description_short_en: str | None = Field(default=None, max_length=500)
    description_long_de: str | None = Field(default=None, max_length=5000)
    description_long_en: str | None = Field(default=None, max_length=5000)
    shot_size: str | None = Field(default=None, max_length=40)
    camera_angle: str | None = Field(default=None, max_length=40)
    interior_exterior: str | None = Field(default=None, max_length=40)
    time_of_day: str | None = Field(default=None, max_length=40)
    people_count: int | None = Field(default=None, ge=0, le=1000)
    person_identifiable: Literal["yes", "no", "unknown"] | None = None
    add_tags: list[TagIn] = Field(default_factory=list, max_length=50)
    remove_tags: list[TagIn] = Field(default_factory=list, max_length=50)


class EditOut(BaseModel):
    """What changed."""

    changed: dict[str, Any]
    tags_added: list[str]
    tags_removed: list[str]


class BulkIn(BaseModel):
    """Bulk edit of many assets."""

    asset_ids: list[UUID] = Field(min_length=1, max_length=MAX_BULK)
    add_tags: list[TagIn] = Field(default_factory=list, max_length=20)
    remove_tags: list[TagIn] = Field(default_factory=list, max_length=20)
    locked: bool | None = None
    consent: Literal["yes", "no", "unknown"] | None = None
    consent_ref: str | None = Field(default=None, max_length=300)
    person_identifiable: Literal["yes", "no", "unknown"] | None = None
    add_to_collection: UUID | None = None
    remove_from_collection: UUID | None = None


class BulkOut(BaseModel):
    """Number of assets that were processed."""

    updated: int


class ReviewItem(BaseModel):
    """An asset waiting for review."""

    asset: AssetSummary
    reasons: list[str]


class ReviewPage(BaseModel):
    """Keyset-paginated review queue."""

    items: list[ReviewItem]
    next_cursor: str | None
    total: int | None


class CollectionIn(BaseModel):
    """New collection."""

    name: str = Field(min_length=1, max_length=100)


class CollectionOut(BaseModel):
    """A collection with its size."""

    id: UUID
    name: str
    assets: int
    created_at: datetime


class SegmentOut(BaseModel):
    """A transcript segment."""

    idx: int
    start_ms: int
    end_ms: int
    text: str
    language: str | None
    speaker: str | None
    words: list[dict[str, Any]]


class AudioEventOut(BaseModel):
    """A detected audio event."""

    tag: str
    start_ms: int
    end_ms: int
    confidence: float


class TranscriptOut(BaseModel):
    """Transcript segments (optionally filtered) and audio events of an asset."""

    segments: list[SegmentOut]
    events: list[AudioEventOut]
    total_segments: int


async def _refresh(runtime: Runtime, db: AsyncSession, asset_id: UUID) -> None:
    await ai_sidecar.refresh_for_runtime(runtime, db, asset_id)


def _edit_error(exc: editing.EditError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("/review", response_model=ReviewPage, summary="Assets waiting for review")
async def review_queue(
    _: EditorDep,
    db: DbDep,
    filters: FiltersDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=catalog.MAX_LIMIT)] = 30,
) -> ReviewPage:
    """Pending assets, oldest first, with the reasons the AI gave. Filters narrow the queue."""
    try:
        page = await catalog.list_assets(
            db, replace(filters, review_state="pending", status=None), "created_at", cursor, limit
        )
    except catalog.InvalidQueryError as exc:
        raise HTTPException(422, str(exc)) from exc
    return ReviewPage(
        items=[
            ReviewItem(
                asset=AssetSummary(**_summary(asset, path, label)),
                reasons=list(asset.review_reasons),
            )
            for asset, path, label in page.rows
        ],
        next_cursor=page.next_cursor,
        total=page.total,
    )


@router.patch("/assets/{asset_id}", response_model=EditOut, summary="Edit an asset (human)")
async def edit_asset(
    asset_id: UUID,
    body: AssetEditIn,
    principal: EditorDep,
    request: Request,
    db: DbDep,
    runtime: RuntimeDep,
) -> EditOut:
    """Change descriptions, attributes, rating, lock, consent or tags; source is `human`.

    Edited AI fields are protected from reprocessing; core corrections feed the golden set.
    """
    asset = await db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    data = body.model_dump(exclude_unset=True, exclude={"add_tags", "remove_tags", "review_done"})
    edit = editing.Edit(
        columns=data,
        add_tags=[editing.TagRef(t.field, t.value) for t in body.add_tags],
        remove_tags=[editing.TagRef(t.field, t.value) for t in body.remove_tags],
        review_done=body.review_done,
    )
    try:
        details = await editing.edit_target(
            db, principal.user.id, client_ip(request), asset, None, edit
        )
    except editing.EditError as exc:
        raise _edit_error(exc) from exc
    await db.commit()
    await _refresh(runtime, db, asset_id)
    return EditOut(
        changed=details["columns"],
        tags_added=details.get("tags_added", []),
        tags_removed=details.get("tags_removed", []),
    )


@router.patch("/shots/{shot_id}", response_model=EditOut, summary="Edit a shot (human)")
async def edit_shot(
    shot_id: UUID,
    body: ShotEditIn,
    principal: EditorDep,
    request: Request,
    db: DbDep,
    runtime: RuntimeDep,
) -> EditOut:
    """Change a shot's description, attributes or tags; source is `human`."""
    shot = await db.get(Shot, shot_id)
    if shot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "shot not found")
    asset = await db.get_one(Asset, shot.asset_id)
    edit = editing.Edit(
        columns=body.model_dump(exclude_unset=True, exclude={"add_tags", "remove_tags"}),
        add_tags=[editing.TagRef(t.field, t.value) for t in body.add_tags],
        remove_tags=[editing.TagRef(t.field, t.value) for t in body.remove_tags],
    )
    try:
        details = await editing.edit_target(
            db, principal.user.id, client_ip(request), asset, shot, edit
        )
    except editing.EditError as exc:
        raise _edit_error(exc) from exc
    await db.commit()
    await _refresh(runtime, db, asset.id)
    return EditOut(
        changed=details["columns"],
        tags_added=details.get("tags_added", []),
        tags_removed=details.get("tags_removed", []),
    )


@router.post("/assets/bulk", response_model=BulkOut, summary="Bulk edit assets")
async def bulk_edit(
    body: BulkIn, principal: EditorDep, request: Request, db: DbDep, runtime: RuntimeDep
) -> BulkOut:
    """Edit up to 500 assets: tags, lock, consent, person flag and collection membership."""
    assets = (await db.execute(select(Asset).where(Asset.id.in_(body.asset_ids)))).scalars().all()
    for collection_id in (body.add_to_collection, body.remove_from_collection):
        if collection_id and await db.get(Collection, collection_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "collection not found")
    edit = editing.Edit(
        columns=body.model_dump(
            include={"locked", "consent", "consent_ref", "person_identifiable"},
            exclude_none=True,
        ),
        add_tags=[editing.TagRef(t.field, t.value) for t in body.add_tags],
        remove_tags=[editing.TagRef(t.field, t.value) for t in body.remove_tags],
    )
    try:
        for asset in assets:
            await editing.edit_target(db, principal.user.id, client_ip(request), asset, None, edit)
    except editing.EditError as exc:
        raise _edit_error(exc) from exc
    if body.add_to_collection:
        await db.execute(
            insert(CollectionAsset)
            .values([{"collection_id": body.add_to_collection, "asset_id": a.id} for a in assets])
            .on_conflict_do_nothing()
        )
    if body.remove_from_collection:
        await db.execute(
            delete(CollectionAsset).where(
                CollectionAsset.collection_id == body.remove_from_collection,
                CollectionAsset.asset_id.in_([a.id for a in assets]),
            )
        )
    audit.record(
        db,
        actor_kind="user",
        actor_id=principal.user.id,
        action="asset.bulk_edit",
        details={
            "assets": len(assets),
            "add_tags": [f"{t.field}:{t.value}" for t in body.add_tags],
            "remove_tags": [f"{t.field}:{t.value}" for t in body.remove_tags],
            "locked": body.locked,
            "consent": body.consent,
            "person_identifiable": body.person_identifiable,
            "add_to_collection": str(body.add_to_collection) if body.add_to_collection else None,
        },
        ip=client_ip(request),
    )
    await db.commit()
    for asset in assets:
        await _refresh(runtime, db, asset.id)
    return BulkOut(updated=len(assets))


@router.get("/collections", response_model=list[CollectionOut])
async def list_collections(_: ViewerDep, db: DbDep) -> list[CollectionOut]:
    """Collections with their asset counts."""
    rows = await db.execute(
        select(Collection, func.count(CollectionAsset.asset_id))
        .outerjoin(CollectionAsset, CollectionAsset.collection_id == Collection.id)
        .group_by(Collection.id)
        .order_by(func.lower(Collection.name))
    )
    return [CollectionOut(id=c.id, name=c.name, assets=n, created_at=c.created_at) for c, n in rows]


@router.post("/collections", response_model=CollectionOut, status_code=201)
async def create_collection(
    body: CollectionIn, principal: EditorDep, request: Request, db: DbDep
) -> CollectionOut:
    """Create a collection (names are unique, case-insensitive)."""
    name = body.name.strip()
    taken = await db.scalar(
        select(Collection.id).where(func.lower(Collection.name) == name.lower())
    )
    if taken is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "a collection with this name exists")
    collection = Collection(name=name, created_by=principal.user.id)
    db.add(collection)
    await db.flush()
    audit.record(
        db,
        actor_kind="user",
        actor_id=principal.user.id,
        action="collection.create",
        target_kind="collection",
        target_id=collection.id,
        details={"name": name},
        ip=client_ip(request),
    )
    await db.commit()
    return CollectionOut(
        id=collection.id, name=collection.name, assets=0, created_at=collection.created_at
    )


@router.delete("/collections/{collection_id}", status_code=204)
async def delete_collection(
    collection_id: UUID, principal: AdminDep, request: Request, db: DbDep
) -> None:
    """Delete a collection (its assets stay)."""
    collection = await db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "collection not found")
    await db.delete(collection)
    audit.record(
        db,
        actor_kind="user",
        actor_id=principal.user.id,
        action="collection.delete",
        target_kind="collection",
        target_id=collection_id,
        details={"name": collection.name},
        ip=client_ip(request),
    )
    await db.commit()


@router.get("/assets/{asset_id}/transcript", response_model=TranscriptOut)
async def transcript(
    asset_id: UUID,
    _: ViewerDep,
    db: DbDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> TranscriptOut:
    """Transcript segments (with word timings) and audio events; `q` filters segments."""
    stmt = select(TranscriptSegment).where(TranscriptSegment.asset_id == asset_id)
    total = await db.scalar(
        select(func.count())
        .select_from(TranscriptSegment)
        .where(TranscriptSegment.asset_id == asset_id)
    )
    if q and q.strip():
        stmt = stmt.where(TranscriptSegment.text.icontains(q.strip(), autoescape=True))
    segments = (await db.execute(stmt.order_by(TranscriptSegment.idx).limit(2000))).scalars().all()
    events = (
        (
            await db.execute(
                select(AudioEvent)
                .where(AudioEvent.asset_id == asset_id)
                .order_by(AudioEvent.start_ms)
            )
        )
        .scalars()
        .all()
    )
    return TranscriptOut(
        segments=[
            SegmentOut(
                idx=s.idx,
                start_ms=s.start_ms,
                end_ms=s.end_ms,
                text=s.text,
                language=s.language,
                speaker=s.speaker,
                words=list(s.words),
            )
            for s in segments
        ],
        events=[
            AudioEventOut(tag=e.tag, start_ms=e.start_ms, end_ms=e.end_ms, confidence=e.confidence)
            for e in events
        ],
        total_segments=int(total or 0),
    )
