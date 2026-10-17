"""Delete to trash (administrators, web interface only) and the trash page."""

from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from rushbase_server.api.deps import DbDep, RuntimeDep, WebAdminDep
from rushbase_server.runtime import Runtime
from rushbase_server.services import ai_sidecar, trash

router = APIRouter(prefix="/api/v1", tags=["trash"])


class TrashIn(BaseModel):
    """The explicit confirmation of the dialog."""

    confirm: bool = Field(description="Must be true: the caller confirmed the dialog.")


class TrashedOut(BaseModel):
    """A trashed asset."""

    asset_id: UUID
    filename: str
    media_type: str
    size_total: int
    prior_status: str
    trashed_at: datetime
    expires_at: datetime
    files: int


class TrashList(BaseModel):
    """The trash with its retention period."""

    retention_days: int
    items: list[TrashedOut]


def _root(runtime: Runtime) -> Path:
    if runtime.binding.root is None or not runtime.binding.mounted:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "no library mounted")
    return runtime.binding.root


@router.post(
    "/assets/{asset_id}/trash",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Move an asset to the trash",
)
async def trash_asset(
    asset_id: UUID, body: TrashIn, admin: WebAdminDep, runtime: RuntimeDep, db: DbDep
) -> None:
    """Move the files of a filed asset or duplicate to `.rushbase/trash/` (reversible)."""
    if not body.confirm:
        raise HTTPException(422, "confirmation required")
    try:
        await trash.trash_asset(db, _root(runtime), asset_id, admin.user.id, runtime.clock.now())
    except trash.TrashError as exc:
        code = 404 if str(exc) == "asset not found" else status.HTTP_409_CONFLICT
        raise HTTPException(code, str(exc)) from exc
    await db.commit()
    runtime.cache.clear()


@router.get("/admin/trash", response_model=TrashList, summary="List the trash")
async def list_trash(_: WebAdminDep, db: DbDep) -> TrashList:
    """Trashed assets with their expiry."""
    return TrashList(
        retention_days=await trash.retention_days(db),
        items=[TrashedOut(**vars(i)) for i in await trash.list_trash(db)],
    )


@router.post(
    "/admin/trash/{asset_id}/restore",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Restore an asset from the trash",
)
async def restore(asset_id: UUID, admin: WebAdminDep, runtime: RuntimeDep, db: DbDep) -> None:
    """Move the files back; 409 with the occupied locations when an original path is taken."""
    try:
        await trash.restore_asset(db, _root(runtime), asset_id, admin.user.id, runtime.clock.now())
    except trash.TrashError as exc:
        message = f"{exc}: {', '.join(exc.conflicts)}" if exc.conflicts else str(exc)
        raise HTTPException(status.HTTP_409_CONFLICT, message) from exc
    await db.commit()
    runtime.cache.clear()
    await ai_sidecar.refresh_for_runtime(runtime, db, asset_id)
