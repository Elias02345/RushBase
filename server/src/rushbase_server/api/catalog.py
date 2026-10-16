"""Read APIs for the UI: assets, facets, statistics, batches and derived file serving."""

from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.api.deps import DbDep, RuntimeDep, ViewerDep
from rushbase_server.api.system import StageBacklogOut
from rushbase_server.models import (
    Asset,
    AssetFile,
    AssetMetadata,
    AssetTag,
    AudioEvent,
    Batch,
    Shot,
    TaxField,
    TaxValue,
    TranscriptSegment,
)
from rushbase_server.services import catalog, derived, stats
from rushbase_server.services.settings_store import PATH_ROOTS_KEY, get_setting

router = APIRouter(prefix="/api/v1", tags=["catalog"])


class AssetSummary(BaseModel):
    """Compact asset representation for lists and grids."""

    id: UUID
    media_type: str
    status: str
    title: str | None
    filename: str
    capture_at: datetime | None
    duration_ms: int | None
    width: int | None
    height: int | None
    fps: float | None
    codec: str | None
    camera_model: str | None
    lens_model: str | None
    log_profile: str | None
    orientation: str | None
    size_total: int
    poster_url: str | None
    sprite_url: str | None
    sprite_tiles: int | None
    batch_label: str | None
    review_state: str = "none"
    locked: bool = False
    summary: str | None = None


class AssetPage(BaseModel):
    """Keyset-paginated list; ``total`` is only set on the first page."""

    items: list[AssetSummary]
    next_cursor: str | None
    total: int | None


class FileOut(BaseModel):
    """A file of an asset with its path on every configured operating system."""

    role: str
    rel_path: str
    size: int
    blake3: str | None
    paths: dict[str, str]


class MetadataOut(BaseModel):
    """Normalized probe fields (the raw tool output stays in the sidecar and database)."""

    normalized: dict[str, Any]


class KeyframeOut(BaseModel):
    """A shot keyframe with its URL."""

    t_ms: int
    url: str | None
    position: str


class TagOut(BaseModel):
    """A taxonomy tag with provenance."""

    id: UUID
    field_key: str
    field_label_de: str
    field_label_en: str
    value: str
    label_de: str | None
    label_en: str | None
    source: str
    confidence: float | None
    shot_idx: int | None


class ShotAiOut(BaseModel):
    """AI description and attributes of a shot."""

    description_short_de: str | None
    description_short_en: str | None
    description_long_de: str | None
    description_long_en: str | None
    shot_size: str | None
    camera_angle: str | None
    interior_exterior: str | None
    time_of_day: str | None
    people_count: int | None
    person_identifiable: str
    human_fields: list[str]


class ShotOut(BaseModel):
    """A detected shot."""

    id: UUID
    idx: int
    start_ms: int
    end_ms: int
    keyframes: list[KeyframeOut]
    camera_motion: str | None
    sharpness: float | None
    shake: float | None
    ai: ShotAiOut | None = None


class AiOut(BaseModel):
    """AI and review state of the asset."""

    summary_short_de: str | None
    summary_short_en: str | None
    summary_long_de: str | None
    summary_long_en: str | None
    shot_size: str | None
    camera_angle: str | None
    interior_exterior: str | None
    time_of_day: str | None
    people_count: int | None
    person_identifiable: str
    consent: str
    consent_ref: str | None
    rating: int | None
    locked: bool
    review_state: str
    review_reasons: list[str]
    human_fields: list[str]
    audio: dict[str, Any] | None
    models: dict[str, Any]
    tags: list[TagOut]
    transcript_segments: int
    audio_events: int


class HistoryOut(BaseModel):
    """An audit trail entry about the asset."""

    at: datetime
    action: str
    details: dict[str, Any]


class AssetDetail(AssetSummary):
    """Everything the detail view shows."""

    files: list[FileOut]
    metadata: MetadataOut
    shots: list[ShotOut]
    proxy_url: str | None
    waveform_url: str | None
    content_rel_dir: str | None
    duplicate_of: UUID | None
    tech_summary_de: str | None
    tech_summary_en: str | None
    decode_status: str
    history: list[HistoryOut]
    ai: AiOut


async def ai_out(db: AsyncSession, asset: Asset) -> AiOut:
    """The AI/review part of an asset detail (tags with provenance, counts)."""
    tags = (
        await db.execute(
            select(AssetTag, TaxField, TaxValue, Shot.idx)
            .join(TaxField, TaxField.id == AssetTag.field_id)
            .join(TaxValue, TaxValue.id == AssetTag.value_id)
            .outerjoin(Shot, Shot.id == AssetTag.shot_id)
            .where(AssetTag.asset_id == asset.id)
            .order_by(TaxField.sort, TaxField.key, TaxValue.value)
        )
    ).all()
    segments = await db.scalar(
        select(func.count())
        .select_from(TranscriptSegment)
        .where(TranscriptSegment.asset_id == asset.id)
    )
    events = await db.scalar(
        select(func.count()).select_from(AudioEvent).where(AudioEvent.asset_id == asset.id)
    )
    return AiOut(
        summary_short_de=asset.summary_short,
        summary_short_en=asset.summary_short_en,
        summary_long_de=asset.summary_long_de,
        summary_long_en=asset.summary_long_en,
        shot_size=asset.shot_size,
        camera_angle=asset.camera_angle,
        interior_exterior=asset.interior_exterior,
        time_of_day=asset.time_of_day,
        people_count=asset.people_count,
        person_identifiable=asset.person_identifiable,
        consent=asset.consent,
        consent_ref=asset.consent_ref,
        rating=asset.rating,
        locked=asset.locked,
        review_state=asset.review_state,
        review_reasons=list(asset.review_reasons),
        human_fields=list(asset.human_fields),
        audio=asset.audio_info,
        models=asset.ai_details.get("models", {}),
        tags=[
            TagOut(
                id=tag.id,
                field_key=field.key,
                field_label_de=field.label_de,
                field_label_en=field.label_en,
                value=value.value,
                label_de=value.label_de,
                label_en=value.label_en,
                source=tag.source,
                confidence=tag.confidence,
                shot_idx=idx,
            )
            for tag, field, value, idx in tags
        ],
        transcript_segments=int(segments or 0),
        audio_events=int(events or 0),
    )


def _summary(asset: Asset, primary: str | None, label: str | None) -> dict[str, Any]:
    links = catalog.derived_links(asset)
    return {
        "id": asset.id,
        "media_type": asset.media_type,
        "status": asset.status,
        "title": asset.title,
        "filename": (primary or "").rsplit("/", 1)[-1],
        "capture_at": asset.capture_at,
        "duration_ms": asset.duration_ms,
        "width": asset.width,
        "height": asset.height,
        "fps": float(asset.fps) if asset.fps is not None else None,
        "codec": asset.codec,
        "camera_model": asset.camera_model,
        "lens_model": asset.lens_model,
        "log_profile": asset.log_profile,
        "orientation": asset.orientation,
        "size_total": asset.size_total,
        "poster_url": links["poster_url"],
        "sprite_url": links["sprite_url"],
        "sprite_tiles": links["sprite_tiles"],
        "batch_label": label,
        "review_state": asset.review_state,
        "locked": asset.locked,
        "summary": asset.summary_short,
    }


def _filters(
    media_type: str | None = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
    batch_id: UUID | None = None,
    camera_model: str | None = None,
    lens_model: str | None = None,
    log_profile: str | None = None,
    orientation: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    shot_size: str | None = None,
    camera_angle: str | None = None,
    interior_exterior: str | None = None,
    time_of_day: str | None = None,
    person_identifiable: str | None = None,
    review_state: str | None = None,
    consent: str | None = None,
    review_reason: str | None = None,
    collection_id: UUID | None = None,
    tag: Annotated[list[str] | None, Query(max_length=50)] = None,
) -> catalog.AssetFilters:
    """Filter and search parameters shared by the asset list and the facets.

    `tag` is repeatable (`tag=location:harbour`): values of one field are alternatives, different
    fields combine with AND.
    """
    return catalog.AssetFilters(
        media_type, status_, batch_id, camera_model, lens_model, log_profile, orientation,
        date_from, date_to, q, shot_size, camera_angle, interior_exterior, time_of_day,
        person_identifiable, review_state, consent, review_reason, collection_id, tuple(tag or ()),
    )  # fmt: skip


FiltersDep = Annotated[catalog.AssetFilters, Depends(_filters)]


@router.get("/assets", response_model=AssetPage, summary="List assets")
async def list_assets(
    _: ViewerDep,
    db: DbDep,
    filters: FiltersDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=catalog.MAX_LIMIT)] = catalog.DEFAULT_LIMIT,
    sort: str | None = None,
) -> AssetPage:
    """Filter, full-text search (`q`) and page through assets.

    `q` is accent-insensitive full-text search over title, file name, batch, summaries, camera
    data and paths, with prefix matching on the last word; queries under three characters match
    file paths by substring. Without `sort`, searches are ordered by relevance.
    """
    try:
        page = await catalog.list_assets(db, filters, sort, cursor, limit)
    except catalog.InvalidQueryError as exc:
        raise HTTPException(422, str(exc)) from exc
    return AssetPage(
        items=[AssetSummary(**_summary(a, p, label)) for a, p, label in page.rows],
        next_cursor=page.next_cursor,
        total=page.total,
    )


@router.get("/assets/{asset_id}", response_model=AssetDetail, summary="Asset details")
async def get_asset(asset_id: UUID, _: ViewerDep, db: DbDep) -> AssetDetail:
    """Files (with per-OS paths), metadata, shots, derived links and history of one asset."""
    row = (
        await db.execute(
            select(Asset, AssetFile.rel_path, Batch.label)
            .outerjoin(AssetFile, AssetFile.id == Asset.primary_file_id)
            .outerjoin(Batch, Batch.id == Asset.batch_id)
            .where(Asset.id == asset_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    asset, primary, label = row
    roots = await get_setting(db, PATH_ROOTS_KEY, {})
    files = (
        (
            await db.execute(
                select(AssetFile).where(AssetFile.asset_id == asset_id).order_by(AssetFile.rel_path)
            )
        )
        .scalars()
        .all()
    )
    metadata = await db.get(AssetMetadata, asset_id)
    shots = (
        (await db.execute(select(Shot).where(Shot.asset_id == asset_id).order_by(Shot.idx)))
        .scalars()
        .all()
    )
    links = catalog.derived_links(asset)
    return AssetDetail(
        **_summary(asset, primary, label),
        files=[
            FileOut(
                role=f.role,
                rel_path=f.rel_path,
                size=f.size,
                blake3=f.blake3,
                paths=catalog.platform_paths(roots, f.rel_path),
            )
            for f in files
        ],
        metadata=MetadataOut(normalized=metadata.normalized if metadata else {}),
        shots=[
            ShotOut(
                id=s.id,
                idx=s.idx,
                start_ms=s.start_ms,
                end_ms=s.end_ms,
                keyframes=[
                    KeyframeOut(
                        t_ms=k["t_ms"],
                        url=derived.derived_url(asset_id, k["rel_path"]),
                        position=k["position"],
                    )
                    for k in s.keyframes
                ],
                camera_motion=s.camera_motion,
                sharpness=s.sharpness,
                shake=s.shake,
                ai=ShotAiOut(
                    description_short_de=s.description_short_de or s.description_short,
                    description_short_en=s.description_short_en,
                    description_long_de=s.description_long_de,
                    description_long_en=s.description_long_en,
                    shot_size=s.shot_size,
                    camera_angle=s.angle,
                    interior_exterior=s.interior_exterior,
                    time_of_day=s.time_of_day,
                    people_count=s.people_count,
                    person_identifiable=s.person_identifiable,
                    human_fields=list(s.human_fields),
                ),
            )
            for s in shots
        ],
        proxy_url=links["proxy_url"],
        waveform_url=links["waveform_url"],
        content_rel_dir=asset.content_rel_dir,
        duplicate_of=asset.duplicate_of,
        tech_summary_de=asset.tech_summary_de,
        tech_summary_en=asset.tech_summary_en,
        decode_status=asset.decode_status,
        history=[
            HistoryOut(at=e.at, action=e.action, details=e.details)
            for e in await catalog.history(db, asset_id)
        ],
        ai=await ai_out(db, asset),
    )


class FacetValue(BaseModel):
    """One value of an enum facet with its asset count."""

    value: str
    count: int


class Facet(BaseModel):
    """A filter dimension. Enum facets list values; range and date facets give min/max."""

    key: str
    label_de: str
    label_en: str
    kind: str = Field(pattern="^(enum|range|date)$")
    values: list[FacetValue] | None = None
    min: float | str | None = None
    max: float | str | None = None


class FacetsOut(BaseModel):
    """All facets."""

    facets: list[Facet]


@router.get(
    "/facets",
    response_model=FacetsOut,
    summary="Filter facets with counts for the current filters and query",
)
async def get_facets(_: ViewerDep, db: DbDep, filters: FiltersDep) -> FacetsOut:
    """Facets for the filter sidebar, computed from the data."""
    return FacetsOut(facets=[Facet(**f) for f in await catalog.facets(db, filters)])


class KeyCount(BaseModel):
    """Bytes and asset count of a media type."""

    key: str
    bytes: int
    count: int


class YearCount(BaseModel):
    """Bytes and asset count of a capture year."""

    year: int
    bytes: int
    count: int


class StorageOut(BaseModel):
    """Storage breakdown."""

    total_bytes: int
    by_media_type: list[KeyCount]
    by_year: list[YearCount]


class DuplicatesOut(BaseModel):
    """Duplicate assets and the space they waste."""

    count: int
    wasted_bytes: int


class StatsOut(BaseModel):
    """Library statistics."""

    storage: StorageOut
    duplicates: DuplicatesOut
    backlog: dict[str, StageBacklogOut]


@router.get("/stats", response_model=StatsOut, summary="Library statistics")
async def get_stats(_: ViewerDep, runtime: RuntimeDep, db: DbDep) -> StatsOut:
    """Storage by media type and year, duplicate waste and the processing backlog."""
    data = await catalog.storage_stats(db)
    backlog = await stats.stage_backlog(db)
    return StatsOut(
        storage=StorageOut(**data["storage"]),
        duplicates=DuplicatesOut(**data["duplicates"]),
        backlog={name: StageBacklogOut(**vars(b)) for name, b in backlog.items()},
    )


class BatchSummary(BaseModel):
    """An ingest batch with its asset counts."""

    id: UUID
    input_path: str
    label: str
    hint_date: date | None
    status: str
    priority: int
    paused: bool
    first_seen_at: datetime
    completed_at: datetime | None
    assets: int
    size_total: int
    by_status: dict[str, int]


@router.get("/batches", response_model=list[BatchSummary], summary="List batches")
async def list_batches(_: ViewerDep, db: DbDep) -> list[BatchSummary]:
    """Batches (newest first) with the number of assets per status."""
    counts: dict[UUID, dict[str, int]] = {}
    sizes: dict[UUID, int] = {}
    rows = await db.execute(
        select(Asset.batch_id, Asset.status, func.count(), func.sum(Asset.size_total))
        .where(Asset.batch_id.is_not(None))
        .group_by(Asset.batch_id, Asset.status)
    )
    for batch_id, state, count, size in rows:
        assert batch_id is not None  # filtered in the query
        counts.setdefault(batch_id, {})[state] = count
        sizes[batch_id] = sizes.get(batch_id, 0) + int(size or 0)
    batches = (
        (await db.execute(select(Batch).order_by(Batch.first_seen_at.desc(), Batch.id.desc())))
        .scalars()
        .all()
    )
    return [
        BatchSummary(
            id=b.id,
            input_path=b.input_path,
            label=b.label,
            hint_date=b.hint_date,
            status=b.status,
            priority=b.priority,
            paused=b.paused,
            first_seen_at=b.first_seen_at,
            completed_at=b.completed_at,
            assets=sum(counts.get(b.id, {}).values()),
            size_total=sizes.get(b.id, 0),
            by_status=counts.get(b.id, {}),
        )
        for b in batches
    ]


@router.get(
    "/derived/{asset_id}/{path:path}", summary="Derived preview files", response_class=FileResponse
)
async def get_derived(asset_id: UUID, path: str, _: ViewerDep, runtime: RuntimeDep) -> FileResponse:
    """Serve a preview (poster, proxy, waveform, keyframe) with HTTP Range support.

    Only files below ``.rushbase/derived/<asset-id>/`` are reachable; originals never are.
    """
    root = runtime.binding.root
    if root is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "no library mounted")
    try:
        file = derived.resolve_derived(root, asset_id, path)
    except derived.DerivedPathError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found") from exc
    return FileResponse(
        file,
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )
