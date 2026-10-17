"""Administration: enrollment tokens, workers, queue control, reprocessing and backups."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from rushbase_core import Stage
from rushbase_server.api.deps import AdminDep, DbDep, EditorDep, RuntimeDep, ViewerDep, client_ip
from rushbase_server.models import ApiToken, Batch, EnrollmentToken, Worker
from rushbase_server.services import audit, backup, queue
from rushbase_server.services.security import hash_token, new_secret
from rushbase_server.services.settings_store import PIPELINE_STAGES_KEY, set_setting

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class EnrollmentIn(BaseModel):
    """Request for a one-time enrollment token."""

    note: str = Field(default="", max_length=200)
    ttl_hours: float | None = Field(default=None, gt=0, le=24 * 30)


class EnrollmentOut(BaseModel):
    """The token is shown exactly once; it is stored hashed."""

    id: UUID
    token: str
    expires_at: datetime


class EnrollmentTokenOut(BaseModel):
    """An enrollment token without its secret (which is only shown at creation)."""

    id: UUID
    note: str
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None


class WorkerOut(BaseModel):
    """A registered worker."""

    id: UUID
    name: str
    hostname: str | None
    platform: str | None
    version: str | None
    status: str
    online: bool
    last_seen_at: datetime | None
    last_stage: str | None
    current_job_id: UUID | None
    capabilities: dict[str, Any]


@router.post(
    "/enrollment-tokens",
    response_model=EnrollmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a one-time worker enrollment token",
)
async def create_enrollment_token(
    body: EnrollmentIn, admin: AdminDep, request: Request, runtime: RuntimeDep, db: DbDep
) -> EnrollmentOut:
    """Create a token a new worker exchanges for its own API key."""
    plaintext = f"rbe_{new_secret(32)}"
    ttl = body.ttl_hours or runtime.settings.enrollment_ttl_hours
    token = EnrollmentToken(
        token_hash=hash_token(plaintext),
        note=body.note,
        created_by=admin.user.id,
        expires_at=runtime.clock.now() + timedelta(hours=ttl),
    )
    db.add(token)
    await db.flush()
    audit.record(
        db,
        actor_kind="user",
        actor_id=admin.user.id,
        action="enrollment_token.create",
        target_kind="enrollment_token",
        target_id=token.id,
        ip=client_ip(request),
    )
    await db.commit()
    return EnrollmentOut(id=token.id, token=plaintext, expires_at=token.expires_at)


@router.get(
    "/enrollment-tokens",
    response_model=list[EnrollmentTokenOut],
    summary="List enrollment tokens",
)
async def list_enrollment_tokens(_: AdminDep, db: DbDep) -> list[EnrollmentTokenOut]:
    """Newest first; the token secrets are never returned again."""
    rows = (
        (await db.execute(select(EnrollmentToken).order_by(EnrollmentToken.created_at.desc())))
        .scalars()
        .all()
    )
    return [
        EnrollmentTokenOut(
            id=t.id,
            note=t.note,
            created_at=t.created_at,
            expires_at=t.expires_at,
            used_at=t.used_at,
            revoked_at=t.revoked_at,
        )
        for t in rows
    ]


@router.post(
    "/enrollment-tokens/{token_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an unused enrollment token",
)
async def revoke_enrollment_token(
    token_id: UUID, admin: AdminDep, request: Request, runtime: RuntimeDep, db: DbDep
) -> None:
    """Revoke an unused token (idempotent, keeps its expiry). Used tokens cannot be revoked."""
    token = await db.get(EnrollmentToken, token_id)
    if token is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "enrollment token not found")
    if token.used_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "token was already used")
    if token.revoked_at is None:
        token.revoked_at = runtime.clock.now()
        audit.record(
            db,
            actor_kind="user",
            actor_id=admin.user.id,
            action="enrollment_token.revoke",
            target_kind="enrollment_token",
            target_id=token.id,
            ip=client_ip(request),
        )
    await db.commit()


@router.get("/workers", response_model=list[WorkerOut], summary="List workers")
async def list_workers(_: AdminDep, runtime: RuntimeDep, db: DbDep) -> list[WorkerOut]:
    """List all workers with their liveness (``online`` = seen recently)."""
    horizon = runtime.clock.now() - timedelta(seconds=runtime.settings.worker_online_seconds)
    rows = (await db.execute(select(Worker).order_by(Worker.name))).scalars()
    return [
        WorkerOut(
            id=w.id,
            name=w.name,
            hostname=w.hostname,
            platform=w.platform,
            version=w.version,
            status=w.status,
            online=w.status != "offline"
            and w.last_seen_at is not None
            and w.last_seen_at >= horizon,
            last_seen_at=w.last_seen_at,
            last_stage=w.last_stage,
            current_job_id=w.current_job_id,
            capabilities=w.capabilities,
        )
        for w in rows
    ]


@router.post("/workers/{worker_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_worker(
    worker_id: UUID, admin: AdminDep, request: Request, runtime: RuntimeDep, db: DbDep
) -> None:
    """Revoke a worker's API keys and mark it offline; its leases expire normally."""
    worker = await db.get(Worker, worker_id)
    if worker is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "worker not found")
    now = runtime.clock.now()
    await db.execute(
        update(ApiToken)
        .where(ApiToken.worker_id == worker_id, ApiToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    worker.status = "offline"
    audit.record(
        db,
        actor_kind="user",
        actor_id=admin.user.id,
        action="worker.revoke",
        target_kind="worker",
        target_id=worker_id,
        ip=client_ip(request),
    )
    await db.commit()


# --- queue control -------------------------------------------------------------------------


class BatchOut(BaseModel):
    """An ingest batch."""

    id: UUID
    input_path: str
    label: str
    hint_date: str | None
    status: str
    priority: int
    paused: bool
    first_seen_at: datetime
    completed_at: datetime | None


class PriorityIn(BaseModel):
    """New batch priority (higher runs first)."""

    priority: int = Field(ge=-100, le=100)


class StagesIn(BaseModel):
    """Stages to enable for newly ingested assets."""

    stages: list[Stage]


class ReprocessIn(BaseModel):
    """Re-enqueue ``stage`` for assets whose latest result is older than ``below_version``."""

    stage: Stage
    below_version: str = Field(min_length=1, max_length=100)
    batch_id: UUID | None = None


class ReprocessOut(BaseModel):
    """Assets that received a new job."""

    enqueued: int
    asset_ids: list[UUID]


@router.get("/batches", response_model=list[BatchOut], tags=["queue"])
async def list_batches(_: ViewerDep, db: DbDep) -> list[BatchOut]:
    """List batches, oldest first."""
    rows = (await db.execute(select(Batch).order_by(Batch.first_seen_at, Batch.id))).scalars()
    return [
        BatchOut(
            id=b.id,
            input_path=b.input_path,
            label=b.label,
            hint_date=b.hint_date.isoformat() if b.hint_date else None,
            status=b.status,
            priority=b.priority,
            paused=b.paused,
            first_seen_at=b.first_seen_at,
            completed_at=b.completed_at,
        )
        for b in rows
    ]


async def _batch(db: DbDep, batch_id: UUID) -> Batch:
    batch = await db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch not found")
    return batch


@router.post("/queue/pause", status_code=status.HTTP_204_NO_CONTENT, tags=["queue"])
async def pause_queue(admin: AdminDep, db: DbDep) -> None:
    """Stop handing out jobs (running jobs finish)."""
    await queue.set_paused(db, True, admin.user.id)
    await db.commit()


@router.post("/queue/resume", status_code=status.HTTP_204_NO_CONTENT, tags=["queue"])
async def resume_queue(admin: AdminDep, db: DbDep) -> None:
    """Hand out jobs again."""
    await queue.set_paused(db, False, admin.user.id)
    await db.commit()


@router.post("/batches/{batch_id}/pause", status_code=status.HTTP_204_NO_CONTENT, tags=["queue"])
async def pause_batch(batch_id: UUID, editor: EditorDep, db: DbDep) -> None:
    """Pause one batch."""
    await queue.set_batch_paused(db, await _batch(db, batch_id), True, editor.user.id)
    await db.commit()


@router.post("/batches/{batch_id}/resume", status_code=status.HTTP_204_NO_CONTENT, tags=["queue"])
async def resume_batch(batch_id: UUID, editor: EditorDep, db: DbDep) -> None:
    """Resume one batch."""
    await queue.set_batch_paused(db, await _batch(db, batch_id), False, editor.user.id)
    await db.commit()


@router.put("/batches/{batch_id}/priority", status_code=status.HTTP_204_NO_CONTENT, tags=["queue"])
async def set_priority(batch_id: UUID, body: PriorityIn, editor: EditorDep, db: DbDep) -> None:
    """Set a batch priority; open jobs of the batch are re-prioritised."""
    await queue.set_batch_priority(db, await _batch(db, batch_id), body.priority, editor.user.id)
    await db.commit()


@router.put("/pipeline/stages", status_code=status.HTTP_204_NO_CONTENT, tags=["queue"])
async def set_stages(body: StagesIn, admin: AdminDep, db: DbDep) -> None:
    """Choose which stages newly ingested assets get (``finalize`` is always included)."""
    await set_setting(db, PIPELINE_STAGES_KEY, [s.value for s in body.stages], admin.user.id)
    audit.record(
        db,
        actor_kind="user",
        actor_id=admin.user.id,
        action="pipeline.stages",
        details={"stages": [s.value for s in body.stages]},
    )
    await db.commit()


@router.post("/reprocess", response_model=ReprocessOut, tags=["queue"])
async def reprocess(
    body: ReprocessIn, admin: AdminDep, runtime: RuntimeDep, db: DbDep
) -> ReprocessOut:
    """Re-enqueue a stage for assets whose latest result has an older model version."""
    assets = await queue.reprocess_stage(
        db,
        body.stage,
        body.below_version,
        runtime.clock.now(),
        runtime.queue_config,
        body.batch_id,
        admin.user.id,
    )
    await db.commit()
    return ReprocessOut(enqueued=len(assets), asset_ids=assets)


# --- backups -----------------------------------------------------------------------------


class BackupInfoOut(BaseModel):
    """A backup file."""

    name: str
    size: int
    created_at: datetime
    kind: str


@router.get("/backups", response_model=list[BackupInfoOut], tags=["backup"])
async def list_backups(_: AdminDep, runtime: RuntimeDep) -> list[BackupInfoOut]:
    """List backups on the share, newest first."""
    root = runtime.binding.root
    infos = backup.list_backups(backup.backup_dir(root)) if root else []
    return [
        BackupInfoOut(name=b.name, size=b.size, created_at=b.created_at, kind=b.kind) for b in infos
    ]


@router.post(
    "/backups", response_model=BackupInfoOut, status_code=status.HTTP_201_CREATED, tags=["backup"]
)
async def create_backup(
    admin: AdminDep, request: Request, runtime: RuntimeDep, db: DbDep
) -> BackupInfoOut:
    """Create a backup now."""
    try:
        info = await runtime.backup_now()
    except backup.BackupError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    audit.record(
        db,
        actor_kind="user",
        actor_id=admin.user.id,
        action="backup.create",
        details={"file": info.name},
        ip=client_ip(request),
    )
    await db.commit()
    return BackupInfoOut(name=info.name, size=info.size, created_at=info.created_at, kind=info.kind)
