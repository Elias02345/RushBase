"""Confirm or reject audio/video sync suggestions."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from rushbase_server.api.deps import DbDep, EditorDep, RuntimeDep
from rushbase_server.services import ai_sidecar, sync

router = APIRouter(prefix="/api/v1/sync-suggestions", tags=["sync"])


class DecisionIn(BaseModel):
    """The person's decision."""

    decision: Literal["confirmed", "rejected"]


@router.post("/{suggestion_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Decide")
async def decide(
    suggestion_id: UUID, body: DecisionIn, editor: EditorDep, runtime: RuntimeDep, db: DbDep
) -> None:
    """Confirm (links the pair in both sidecars) or reject a sync suggestion."""
    row = await sync.decide(db, suggestion_id, body.decision, editor.user.id, runtime.clock.now())
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "suggestion not found")
    await db.commit()
    for asset_id in (row.asset_a, row.asset_b):
        await ai_sidecar.refresh_for_runtime(runtime, db, asset_id)
