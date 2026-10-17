"""Worker protocol: enrollment, claiming, heartbeats, results (``/api/v1/worker/*``).

Workers pull; none of these calls waits for or requires a worker, and none blocks on one
(ADR-0012). Authentication is a bearer API key issued at enrollment.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from rushbase_core import Stage
from rushbase_server.api.deps import ActiveDep, DbDep, RuntimeDep, WorkerDep, client_ip
from rushbase_server.capabilities import WorkerCapabilities
from rushbase_server.models import ApiToken, EnrollmentToken, Job, Library, Worker
from rushbase_server.services import ai_sidecar, audit, queue
from rushbase_server.services.security import WORKER_TOKEN_PREFIX, hash_token, new_token

AI_STAGES = frozenset({"audio", "embed", "vision", "classify"})

router = APIRouter(prefix="/api/v1/worker", tags=["worker"])


class EnrollIn(BaseModel):
    """Enrollment request with the one-time token from the web UI."""

    token: str = Field(min_length=10, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    hostname: str | None = Field(default=None, max_length=255)
    platform: str | None = Field(default=None, max_length=100)
    version: str | None = Field(default=None, max_length=50)
    capabilities: WorkerCapabilities = Field(default_factory=WorkerCapabilities)


class EnrollOut(BaseModel):
    """The API key is shown exactly once."""

    worker_id: UUID
    api_key: str
    library_id: UUID | None
    heartbeat_seconds: float
    lease_seconds: float


class LibraryOut(BaseModel):
    """Identifies the library so a worker can verify its mount (``library.json``)."""

    library_id: UUID | None
    name: str | None


class ClaimIn(BaseModel):
    """Claim request."""

    max_jobs: int = Field(default=1, ge=1, le=50)
    capabilities: WorkerCapabilities | None = Field(
        default=None, description="Current capabilities; omitted = keep the stored ones."
    )


class JobFile(BaseModel):
    """A file of the job's asset (paths relative to the library root)."""

    role: str
    rel_path: str
    size: int
    card_structure_root: str | None


class JobOut(BaseModel):
    """A leased job."""

    job_id: UUID
    asset_id: UUID
    stage: Stage
    media_type: str
    attempt: int
    lease_expires_at: datetime
    files: list[JobFile]
    upstream: dict[str, dict[str, Any]] = Field(
        description="Latest successful output per earlier stage of this asset."
    )


class ClaimOut(BaseModel):
    """Claimed jobs (possibly none) and the timing the worker should follow."""

    jobs: list[JobOut]
    heartbeat_seconds: float
    lease_seconds: float


class HeartbeatOut(BaseModel):
    """New lease expiry."""

    lease_expires_at: datetime


class ResultIn(BaseModel):
    """Outcome of a job."""

    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=5000)
    retryable: bool = True
    model_id: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    started_at: datetime | None = None


class ResultOut(BaseModel):
    """Whether the result was applied. ``duplicate`` results are ignored safely."""

    accepted: bool
    duplicate: bool
    job_state: str


def _lease_lost(exc: queue.LeaseLostError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, f"lease_lost: {exc}")


@router.post("/enroll", response_model=EnrollOut, status_code=status.HTTP_201_CREATED)
async def enroll(body: EnrollIn, request: Request, runtime: RuntimeDep, db: DbDep) -> EnrollOut:
    """Exchange a one-time enrollment token for a worker API key."""
    now = runtime.clock.now()
    token = (
        await db.execute(
            select(EnrollmentToken)
            .where(EnrollmentToken.token_hash == hash_token(body.token))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        token is None
        or token.used_at is not None
        or token.revoked_at is not None
        or token.expires_at <= now
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "enrollment token invalid or already used")
    worker = Worker(
        name=body.name,
        hostname=body.hostname,
        platform=body.platform,
        version=body.version,
        capabilities=body.capabilities.model_dump(mode="json"),
        status="online",
        last_seen_at=now,
    )
    db.add(worker)
    await db.flush()
    secret = new_token(WORKER_TOKEN_PREFIX)
    db.add(
        ApiToken(
            worker_id=worker.id,
            kind="worker",
            name=body.name,
            token_hash=secret.token_hash,
            prefix=secret.prefix,
        )
    )
    token.used_at = now
    token.used_by_worker = worker.id
    audit.record(
        db,
        actor_kind="worker",
        actor_id=worker.id,
        action="worker.enroll",
        target_kind="worker",
        target_id=worker.id,
        details={"name": body.name, "enrollment_token": str(token.id)},
        ip=client_ip(request),
    )
    await db.commit()
    return EnrollOut(
        worker_id=worker.id,
        api_key=secret.plaintext,
        library_id=runtime.binding.library_id,
        heartbeat_seconds=runtime.settings.job_heartbeat_seconds,
        lease_seconds=runtime.settings.job_lease_seconds,
    )


@router.get("/library", response_model=LibraryOut, summary="Library id for the mount check")
async def library(worker: WorkerDep, runtime: RuntimeDep, db: DbDep) -> LibraryOut:
    """Return the library id; the worker compares it with its mounted ``library.json``."""
    row = (await db.execute(select(Library))).scalars().first()
    worker.last_seen_at = runtime.clock.now()
    await db.commit()
    return LibraryOut(
        library_id=row.id if row else runtime.binding.library_id, name=row.name if row else None
    )


@router.post("/claim", response_model=ClaimOut, summary="Lease jobs matching the capabilities")
async def claim(
    body: ClaimIn, worker: WorkerDep, runtime: RuntimeDep, db: DbDep, _: ActiveDep
) -> ClaimOut:
    """Lease up to ``max_jobs`` jobs, preferring the stage this worker ran last."""
    caps = body.capabilities or WorkerCapabilities.from_json(worker.capabilities)
    claimed = await queue.claim_jobs(
        db, worker, caps, body.max_jobs, runtime.clock.now(), runtime.queue_config
    )
    jobs = [
        JobOut(
            job_id=c.job.id,
            asset_id=c.asset.id,
            stage=Stage(c.job.stage),
            media_type=c.asset.media_type,
            attempt=c.job.attempts + 1,
            lease_expires_at=c.job.lease_expires_at,
            files=[
                JobFile(
                    role=f.role,
                    rel_path=f.rel_path,
                    size=f.size,
                    card_structure_root=f.card_structure_root,
                )
                for f in c.files
            ],
            upstream=c.upstream,
        )
        for c in claimed
    ]
    await db.commit()
    return ClaimOut(
        jobs=jobs,
        heartbeat_seconds=runtime.settings.job_heartbeat_seconds,
        lease_seconds=runtime.settings.job_lease_seconds,
    )


@router.post("/jobs/{job_id}/heartbeat", response_model=HeartbeatOut)
async def heartbeat(
    job_id: UUID, worker: WorkerDep, runtime: RuntimeDep, db: DbDep, _: ActiveDep
) -> HeartbeatOut:
    """Extend the lease. 409 ``lease_lost`` means: stop working on this job."""
    try:
        expires = await queue.heartbeat(
            db, job_id, worker, runtime.clock.now(), runtime.queue_config
        )
    except queue.JobNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found") from exc
    except queue.LeaseLostError as exc:
        raise _lease_lost(exc) from exc
    await db.commit()
    return HeartbeatOut(lease_expires_at=expires)


@router.post("/jobs/{job_id}/result", response_model=ResultOut)
async def submit_result(
    job_id: UUID, body: ResultIn, worker: WorkerDep, runtime: RuntimeDep, db: DbDep, _: ActiveDep
) -> ResultOut:
    """Report success or failure. Repeating the call for a finished job is harmless."""
    data = queue.ResultData(**body.model_dump())
    try:
        outcome = await queue.submit_result(
            db, job_id, worker, data, runtime.clock.now(), runtime.queue_config
        )
    except queue.JobNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found") from exc
    except queue.LeaseLostError as exc:
        raise _lease_lost(exc) from exc
    await db.commit()
    if outcome.accepted and outcome.job_state == "done":
        job = await db.get(Job, job_id)
        if job is not None and job.stage in AI_STAGES:
            await ai_sidecar.refresh_for_runtime(runtime, db, job.asset_id)
            if job.stage == Stage.CLASSIFY.value:
                await runtime.match_needs(job.asset_id)
    return ResultOut(
        accepted=outcome.accepted, duplicate=outcome.duplicate, job_state=outcome.job_state
    )


@router.post("/jobs/{job_id}/release", status_code=status.HTTP_204_NO_CONTENT)
async def release(
    job_id: UUID, worker: WorkerDep, runtime: RuntimeDep, db: DbDep, _: ActiveDep
) -> None:
    """Give a job back without an attempt penalty (graceful shutdown)."""
    try:
        await queue.release_job(db, job_id, worker, runtime.clock.now())
    except queue.JobNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found") from exc
    except queue.LeaseLostError as exc:
        raise _lease_lost(exc) from exc
    await db.commit()
