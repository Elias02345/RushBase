"""Needs lists: what footage is still wanted, and which assets may fulfil it (spec section 11)."""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from rushbase_core.mcp_tools import MediaFilters
from rushbase_server.api.deps import DbDep, EditorDep, ViewerDep, client_ip
from rushbase_server.models import Asset, AssetFile
from rushbase_server.services import audit, catalog, needs

router = APIRouter(prefix="/api/v1/needs", tags=["needs"])


class ItemIn(BaseModel):
    """A wanted shot."""

    description: str = Field(min_length=1, max_length=300)
    filters: MediaFilters = Field(default_factory=MediaFilters)
    count_wanted: int = Field(default=1, ge=1, le=50)


class NeedIn(BaseModel):
    """New needs list."""

    title: str = Field(min_length=1, max_length=200)
    purpose: str = Field(default="", max_length=2000)
    items: list[ItemIn] = Field(default_factory=list, max_length=50)


class NeedPatch(BaseModel):
    """Changes to a list."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    purpose: str | None = Field(default=None, max_length=2000)
    status: Literal["open", "closed"] | None = None


class ItemPatch(BaseModel):
    """Changes to an item."""

    description: str | None = Field(default=None, min_length=1, max_length=300)
    filters: MediaFilters | None = None
    count_wanted: int | None = Field(default=None, ge=1, le=50)
    status: Literal["open", "fulfilled", "dropped"] | None = None


class CandidateIn(BaseModel):
    """Confirm, reject or add a candidate."""

    status: Literal["candidate", "confirmed", "rejected"]
    shot_idx: int | None = Field(default=None, ge=0)


class NeedSummary(BaseModel):
    """List with counts."""

    id: UUID
    title: str
    purpose: str
    status: str
    created_at: str
    items: dict[str, int]
    candidates: int


class NeedPage(BaseModel):
    """One page of lists."""

    items: list[NeedSummary]
    next_offset: int | None


def _convert(item: ItemIn) -> tuple[str, dict[str, Any], int]:
    return item.description, item.filters.model_dump(mode="json"), item.count_wanted


def _guard(exc: needs.NeedsError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("", response_model=NeedPage, summary="List needs lists")
async def list_needs(
    _: ViewerDep,
    db: DbDep,
    status_: Annotated[Literal["open", "closed"] | None, Query(alias="status")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> NeedPage:
    """Lists newest first with item states and the number of open candidates."""
    rows, more = await needs.list_summaries(db, status_, offset, limit)
    return NeedPage(
        items=[NeedSummary(**row) for row in rows], next_offset=offset + limit if more else None
    )


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a needs list")
async def create_need(body: NeedIn, editor: EditorDep, db: DbDep) -> dict[str, Any]:
    """Create a list with optional items."""
    need = await needs.create_list(
        db, body.title, body.purpose, [_convert(i) for i in body.items], "user", editor.user.id
    )
    await db.commit()
    return await needs.detail(db, need.id)


@router.get("/{need_id}", summary="Needs list with items and candidates")
async def get_need(need_id: UUID, _: ViewerDep, db: DbDep) -> dict[str, Any]:
    """Items with candidates, each with file name and poster for display."""
    try:
        data = await needs.detail(db, need_id)
    except needs.NeedsError as exc:
        raise _guard(exc) from exc
    ids = {UUID(c["asset_id"]) for item in data["items"] for c in item["candidates"]}
    info: dict[str, dict[str, Any]] = {}
    if ids:
        rows = await db.execute(
            select(Asset, AssetFile.rel_path)
            .outerjoin(AssetFile, AssetFile.id == Asset.primary_file_id)
            .where(Asset.id.in_(ids))
        )
        for asset, path in rows:
            info[str(asset.id)] = {
                "filename": (path or "").rsplit("/", 1)[-1],
                "poster_url": catalog.derived_links(asset)["poster_url"],
                "summary": asset.summary_short,
                "media_type": asset.media_type,
            }
    for item in data["items"]:
        for candidate in item["candidates"]:
            candidate["asset"] = info.get(candidate["asset_id"])
    return data


@router.patch("/{need_id}", summary="Edit or close a list")
async def patch_need(
    need_id: UUID, body: NeedPatch, editor: EditorDep, request: Request, db: DbDep
) -> dict[str, Any]:
    """Rename, change the purpose, close or reopen."""
    try:
        need = await needs.load_list(db, need_id)
    except needs.NeedsError as exc:
        raise _guard(exc) from exc
    for name, value in body.model_dump(exclude_unset=True).items():
        setattr(need, name, value)
    audit.record(
        db,
        actor_kind="user",
        actor_id=editor.user.id,
        action="needs.update",
        target_kind="need_list",
        target_id=need.id,
        details=body.model_dump(exclude_unset=True),
        ip=client_ip(request),
    )
    await db.commit()
    return await needs.detail(db, need_id)


@router.post("/{need_id}/items", status_code=status.HTTP_201_CREATED, summary="Add items")
async def add_items(need_id: UUID, body: list[ItemIn], _: EditorDep, db: DbDep) -> dict[str, Any]:
    """Append wanted shots to a list."""
    try:
        need = await needs.load_list(db, need_id)
    except needs.NeedsError as exc:
        raise _guard(exc) from exc
    await needs.add_items(db, need, [_convert(i) for i in body])
    await db.commit()
    return await needs.detail(db, need_id)


@router.patch("/items/{item_id}", summary="Edit an item")
async def patch_item(item_id: UUID, body: ItemPatch, _: EditorDep, db: DbDep) -> dict[str, str]:
    """Change description, filters, wanted count or status."""
    try:
        item = await needs.load_item(db, item_id)
    except needs.NeedsError as exc:
        raise _guard(exc) from exc
    data = body.model_dump(exclude_unset=True, mode="json")
    if "filters" in data:
        item.filters = needs.clean_filters(data.pop("filters"))
    for name, value in data.items():
        setattr(item, name, value)
    await db.commit()
    return {"id": str(item.id), "status": item.status}


@router.put("/items/{item_id}/candidates/{asset_id}", summary="Confirm, reject or add a candidate")
async def put_candidate(
    item_id: UUID, asset_id: UUID, body: CandidateIn, _: EditorDep, db: DbDep
) -> dict[str, str]:
    """Human decision on a candidate (source `human`)."""
    try:
        item = await needs.load_item(db, item_id)
        row = await needs.set_candidate(db, item, asset_id, body.shot_idx, body.status, "human")
    except needs.NeedsError as exc:
        raise _guard(exc) from exc
    await db.commit()
    return {"id": str(row.id), "status": row.status, "item_status": item.status}
