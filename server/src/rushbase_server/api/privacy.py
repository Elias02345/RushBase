"""Privacy tools: lock all assets of a batch or of a person, optionally setting consent."""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from rushbase_server.api.deps import DbDep, EditorDep, RuntimeDep, client_ip
from rushbase_server.services import ai_sidecar, audit, editing, privacy

router = APIRouter(prefix="/api/v1/privacy", tags=["privacy"])


class LockIn(BaseModel):
    """Select a batch or a person (exactly one) and what to set on the matches."""

    batch_id: UUID | None = None
    person: str | None = Field(default=None, max_length=100)
    consent: Literal["yes", "no", "unknown"] | None = None
    consent_ref: str | None = Field(default=None, max_length=300)
    dry_run: bool = Field(default=True, description="Only count the matches; change nothing.")


class LockOut(BaseModel):
    """The matches and how many were changed."""

    matched: int
    updated: int
    sample: list[UUID]


@router.post("/lock", response_model=LockOut, summary="Lock the assets of a batch or person")
async def lock_assets(
    body: LockIn, editor: EditorDep, request: Request, runtime: RuntimeDep, db: DbDep
) -> LockOut:
    """Find the assets of a batch or mentioning/tagged with a person; lock them unless dry run.

    Locked assets keep their person and consent fields when the analysis runs again. Face
    recognition is not involved: a person is a taxonomy value (`person` field) or a name in the
    texts and transcript.
    """
    try:
        assets = await privacy.find_assets(db, batch_id=body.batch_id, person=body.person)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if len(assets) > privacy.MAX_LOCK:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"more than {privacy.MAX_LOCK} matches"
        )
    sample = [a.id for a in assets[:20]]
    if body.dry_run:
        return LockOut(matched=len(assets), updated=0, sample=sample)
    columns: dict[str, Any] = {"locked": True}
    if body.consent is not None:
        columns["consent"] = body.consent
    if body.consent_ref is not None:
        columns["consent_ref"] = body.consent_ref
    ip = client_ip(request)
    for asset in assets:
        await editing.edit_target(
            db, editor.user.id, ip, asset, None, editing.Edit(columns=columns)
        )
    audit.record(
        db,
        actor_kind="user",
        actor_id=editor.user.id,
        action="privacy.lock",
        details={
            "batch_id": str(body.batch_id) if body.batch_id else None,
            "person": body.person,
            "assets": len(assets),
            "consent": body.consent,
        },
        ip=ip,
    )
    await db.commit()
    for asset in assets:
        await ai_sidecar.refresh_for_runtime(runtime, db, asset.id)
    runtime.cache.clear()
    return LockOut(matched=len(assets), updated=len(assets), sample=sample)
