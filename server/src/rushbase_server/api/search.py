"""Hybrid search, capabilities, "find similar" and the re-embed admin action."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.api.catalog import AssetSummary, FiltersDep, _summary
from rushbase_server.api.deps import AdminDep, DbDep, RuntimeDep, ViewerDep
from rushbase_server.models import Asset, Shot
from rushbase_server.search import hybrid, reembed
from rushbase_server.services import derived
from rushbase_server.services.catalog import AssetFilters

router = APIRouter(prefix="/api/v1", tags=["search"])
MAX_RESULTS = 100


class ShotHit(BaseModel):
    """A scene hit: timecodes, keyframe and descriptions."""

    id: UUID
    idx: int
    start_ms: int
    end_ms: int
    keyframe_url: str | None
    description_de: str | None
    description_en: str | None


class SearchHit(BaseModel):
    """An asset (or one of its shots) with the fused score and per-channel ranks."""

    asset: AssetSummary
    shot: ShotHit | None
    score: float
    ranks: dict[str, int]


class SearchOut(BaseModel):
    """One page of fused hits."""

    items: list[SearchHit]
    level: Literal["asset", "shot"]
    offset: int
    next_offset: int | None
    candidates: int
    channels_ms: dict[str, float]
    skipped: dict[str, str]
    """Channels that did not run, with the reason (missing models, no vectors)."""


class SpaceStatus(BaseModel):
    """State of one semantic channel."""

    available: bool
    warming: bool = False
    """The query encoder is still loading in the background (searches wait for it)."""
    model_id: str
    revision: str | None
    reason: str | None
    vectors_compatible: int
    vectors_mismatched: int


class CapabilitiesOut(BaseModel):
    """What search can do right now (it never fails; it degrades)."""

    fulltext: bool
    text_semantic: SpaceStatus
    image_semantic: SpaceStatus
    search_by_image: bool
    reembed_needed: bool


def _keyframe_url(asset: Asset, shot: Shot) -> str | None:
    frames = sorted(shot.keyframes, key=lambda k: k.get("position") != "middle")
    return derived.derived_url(asset.id, frames[0]["rel_path"]) if frames else None


async def _render(
    db: AsyncSession, result: hybrid.SearchResult, level: str, offset: int, limit: int
) -> SearchOut:
    items = [
        SearchHit(
            asset=AssetSummary(**_summary(row.asset, row.filename, row.batch_label)),
            shot=(
                ShotHit(
                    id=row.shot.id,
                    idx=row.shot.idx,
                    start_ms=row.shot.start_ms,
                    end_ms=row.shot.end_ms,
                    keyframe_url=_keyframe_url(row.asset, row.shot),
                    description_de=row.shot.description_short_de or row.shot.description_short,
                    description_en=row.shot.description_short_en,
                )
                if row.shot
                else None
            ),
            score=hit.score,
            ranks=hit.ranks,
        )
        for hit, row in await hybrid.hydrate(db, result.hits)
    ]
    more = offset + limit < result.candidates
    return SearchOut(
        items=items,
        level="shot" if level == "shot" else "asset",
        offset=offset,
        next_offset=offset + limit if more else None,
        candidates=result.candidates,
        channels_ms=result.channels,
        skipped=result.skipped,
    )


@router.get("/search", response_model=SearchOut, summary="Hybrid search")
async def search(
    _: ViewerDep,
    db: DbDep,
    runtime: RuntimeDep,
    filters: FiltersDep,
    level: Literal["asset", "shot"] = "asset",
    offset: Annotated[int, Query(ge=0, le=hybrid.MAX_DEPTH)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 30,
) -> SearchOut:
    """Full-text, text-vector (e5) and image-vector (SigLIP) channels fused by RRF (k = 60).

    Filters apply before ranking. Without models on the server only the full-text channel runs
    (see `/search/capabilities`). `level=shot` returns scenes with timecodes and keyframes.
    """
    if not filters.q or not filters.q.strip():
        raise HTTPException(422, "q is required")
    result = await hybrid.search(
        db, runtime.encoder, filters.q.strip(), level, filters, offset, limit
    )
    return await _render(db, result, level, offset, limit)


@router.get("/search/capabilities", response_model=CapabilitiesOut)
async def capabilities(_: ViewerDep, db: DbDep, runtime: RuntimeDep) -> CapabilitiesOut:
    """Which channels are active, and whether stored vectors match the server's encoder."""

    async def space(name: str) -> SpaceStatus:
        info = runtime.encoder.info(name)
        compatible, mismatched = (
            await runtime.cache.get_or_compute(
                ("vectors", name, info.model_id, info.revision),
                lambda: hybrid.compatible_counts(db, name, info),
            )
            if info.model_id
            else (0, 0)
        )
        return SpaceStatus(
            available=info.available,
            warming=info.warming,
            model_id=info.model_id,
            revision=info.revision,
            reason=info.reason,
            vectors_compatible=compatible,
            vectors_mismatched=mismatched,
        )

    text_status, image_status = await space("text"), await space("image")
    return CapabilitiesOut(
        fulltext=True,
        text_semantic=text_status,
        image_semantic=image_status,
        search_by_image=False,
        reembed_needed=bool(text_status.vectors_mismatched or image_status.vectors_mismatched),
    )


@router.post("/search/by-image", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def search_by_image(_: ViewerDep) -> None:
    """Not available: encoding an uploaded image needs the vision tower, which only workers run."""
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "search by example image needs the SigLIP vision tower; per ADR-0012 the server only "
        "encodes text queries. Use 'find similar' on an existing asset or shot instead.",
    )


async def _similar(
    db: AsyncSession,
    asset_id: UUID,
    shot_id: UUID | None,
    filters: AssetFilters,
    offset: int,
    limit: int,
) -> SearchOut:
    result = await hybrid.similar(db, asset_id, shot_id, filters, offset, limit)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no image vectors stored for this item")
    return await _render(db, result, "shot" if shot_id else "asset", offset, limit)


@router.get("/assets/{asset_id}/similar", response_model=SearchOut)
async def similar_assets(
    asset_id: UUID,
    _: ViewerDep,
    db: DbDep,
    filters: FiltersDep,
    offset: Annotated[int, Query(ge=0, le=hybrid.MAX_DEPTH)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 30,
) -> SearchOut:
    """Assets that look like this one (stored SigLIP vectors, no encoder needed)."""
    return await _similar(db, asset_id, None, filters, offset, limit)


@router.get("/shots/{shot_id}/similar", response_model=SearchOut)
async def similar_shots(
    shot_id: UUID,
    _: ViewerDep,
    db: DbDep,
    filters: FiltersDep,
    offset: Annotated[int, Query(ge=0, le=hybrid.MAX_DEPTH)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 30,
) -> SearchOut:
    """Shots that look like this one."""
    asset_id = await db.scalar(select(Shot.asset_id).where(Shot.id == shot_id))
    if asset_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "shot not found")
    return await _similar(db, asset_id, shot_id, filters, offset, limit)


class ReembedOut(BaseModel):
    """Number of assets queued for re-indexing."""

    assets: int


@router.post("/admin/reembed", response_model=ReembedOut, tags=["admin"])
async def reembed_all(principal: AdminDep, db: DbDep, runtime: RuntimeDep) -> ReembedOut:
    """Queue `embed` + `classify` for assets whose vectors do not match the server's encoder."""
    count = await reembed.enqueue(
        db, runtime.encoder, runtime.clock.now(), runtime.queue_config, principal.user.id
    )
    await db.commit()
    return ReembedOut(assets=count)
