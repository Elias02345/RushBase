"""Job queue: DAG creation, leasing, heartbeats, results, reaper, reprocessing and pausing.

Concurrency model: every state change of a job happens in a transaction that holds a row
lock on the job (``SELECT ... FOR UPDATE``). Claiming uses ``SKIP LOCKED`` so concurrent
workers never wait for each other and never lease the same job twice.

All functions take ``now`` explicitly (no hidden clock) so that lease expiry and backoff can
be tested without sleeping. They never commit; the caller owns the transaction.
"""

import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core import MediaType, Stage, build_job_plan, uuid7, version_lt
from rushbase_server.capabilities import WorkerCapabilities
from rushbase_server.models import (
    Asset,
    AssetFile,
    Batch,
    InputFile,
    Job,
    StageResult,
    Worker,
)
from rushbase_server.services import audit
from rushbase_server.services.settings_store import QUEUE_PAUSED_KEY, get_setting, set_setting
from rushbase_server.settings import Settings

logger = logging.getLogger(__name__)

OPEN_STATES = ("blocked", "queued", "leased")
INACTIVE_ASSET_STATUSES = ("quarantined", "trashed", "duplicate")


class QueueError(Exception):
    """Base class for queue problems."""


class JobNotFoundError(QueueError):
    """No job with the given id."""


class LeaseLostError(QueueError):
    """The job is not (or no longer) leased to the calling worker."""


@dataclass(frozen=True)
class QueueConfig:
    """Tunable queue parameters (see :class:`rushbase_server.settings.Settings`)."""

    lease_seconds: float = 120
    max_attempts: int = 5
    backoff_base_seconds: float = 30
    backoff_cap_seconds: float = 3600

    @classmethod
    def from_settings(cls, settings: Settings) -> "QueueConfig":
        """Build the configuration from server settings."""
        return cls(
            lease_seconds=settings.job_lease_seconds,
            max_attempts=settings.job_max_attempts,
            backoff_base_seconds=settings.job_backoff_base_seconds,
            backoff_cap_seconds=settings.job_backoff_cap_seconds,
        )

    def backoff(self, attempts: int) -> timedelta:
        """Exponential backoff after ``attempts`` failed attempts (1 -> base, 2 -> 2*base, ...)."""
        seconds = self.backoff_base_seconds * (2 ** max(0, attempts - 1))
        return timedelta(seconds=min(seconds, self.backoff_cap_seconds))


@dataclass
class ClaimedJob:
    """A leased job with everything a worker needs to run it."""

    job: Job
    asset: Asset
    files: list[AssetFile]
    upstream: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class ResultData:
    """A worker's report about a finished (or failed) job."""

    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    retryable: bool = True
    model_id: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    duration_ms: int | None = None
    started_at: datetime | None = None


@dataclass(frozen=True)
class ResultOutcome:
    """What happened to a submitted result."""

    accepted: bool
    duplicate: bool
    job_state: str


@dataclass(frozen=True)
class ReapReport:
    """Result of one reaper pass."""

    requeued: int = 0
    failed: int = 0


# --- creation ----------------------------------------------------------------------------


async def create_jobs_for_asset(
    session: AsyncSession,
    asset: Asset,
    batch: Batch | None,
    enabled: Collection[Stage | str],
    config: QueueConfig,
) -> list[Job]:
    """Create the job DAG for ``asset`` and mark it ``processing``.

    Jobs without dependencies start ``queued``, the others ``blocked``. The FIFO key
    ``enqueued_at`` is the batch's first-seen time; ``priority`` inherits the batch priority.
    """
    plan = build_job_plan(enabled, MediaType(asset.media_type))
    ids = {spec.stage: uuid7() for spec in plan}
    jobs = [
        Job(
            id=ids[spec.stage],
            asset_id=asset.id,
            stage=spec.stage.value,
            state="blocked" if spec.depends_on else "queued",
            depends_on=[ids[dep] for dep in spec.depends_on],
            priority=batch.priority if batch else 0,
            enqueued_at=batch.first_seen_at if batch else func.now(),
            max_attempts=config.max_attempts,
            requirements={},
        )
        for spec in plan
    ]
    session.add_all(jobs)
    asset.status = "processing"
    return jobs


# --- pause / priority --------------------------------------------------------------------


async def is_paused(session: AsyncSession) -> bool:
    """Return whether the whole queue is paused."""
    return bool(await get_setting(session, QUEUE_PAUSED_KEY, False))


async def set_paused(session: AsyncSession, paused: bool, user_id: UUID | None = None) -> None:
    """Pause or resume the whole queue (running jobs finish normally)."""
    await set_setting(session, QUEUE_PAUSED_KEY, paused, user_id)
    audit.record(
        session,
        actor_kind="user" if user_id else "system",
        actor_id=user_id,
        action="queue.pause" if paused else "queue.resume",
    )


async def set_batch_paused(
    session: AsyncSession, batch: Batch, paused: bool, user_id: UUID | None = None
) -> None:
    """Pause or resume one batch."""
    batch.paused = paused
    audit.record(
        session,
        actor_kind="user" if user_id else "system",
        actor_id=user_id,
        action="batch.pause" if paused else "batch.resume",
        target_kind="batch",
        target_id=batch.id,
    )


async def set_batch_priority(
    session: AsyncSession, batch: Batch, priority: int, user_id: UUID | None = None
) -> int:
    """Set the batch priority and apply it to its open jobs; returns the number of jobs."""
    batch.priority = priority
    asset_ids = select(Asset.id).where(Asset.batch_id == batch.id)
    result = await session.execute(
        update(Job)
        .where(Job.asset_id.in_(asset_ids), Job.state.in_(("blocked", "queued")))
        .values(priority=priority)
    )
    audit.record(
        session,
        actor_kind="user" if user_id else "system",
        actor_id=user_id,
        action="batch.priority",
        target_kind="batch",
        target_id=batch.id,
        details={"priority": priority},
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# --- leasing -----------------------------------------------------------------------------


def _next_job_statement(
    caps: WorkerCapabilities | None,
    stages: Collection[str],
    prefer_stage: str | None,
    now: datetime,
) -> Any:
    gpu_required = func.coalesce(Job.requirements["gpu"].as_boolean(), False)
    min_vram = func.coalesce(Job.requirements["min_vram_mb"].as_integer(), 0)
    stmt = (
        select(Job)
        .join(Asset, Asset.id == Job.asset_id)
        .outerjoin(Batch, Batch.id == Asset.batch_id)
        .where(
            Job.state == "queued",
            or_(Job.not_before.is_(None), Job.not_before <= now),
            Job.stage.in_(list(stages)),
            Asset.status.notin_(INACTIVE_ASSET_STATUSES),
            func.coalesce(Batch.paused, False).is_(False),
        )
    )
    if caps is not None:
        if not caps.has_gpu:
            stmt = stmt.where(gpu_required.is_(False))
        stmt = stmt.where(min_vram <= (caps.vram_mb or 0))
    order: list[Any] = [Job.priority.desc()]
    if prefer_stage is not None:
        order.append(case((Job.stage == prefer_stage, 0), else_=1))
    order += [Job.enqueued_at, Job.id]
    return (
        stmt.order_by(*order)
        .limit(1)
        .with_for_update(skip_locked=True, of=Job)
        .execution_options(populate_existing=True)
    )


async def claim_jobs(
    session: AsyncSession,
    worker: Worker,
    caps: WorkerCapabilities,
    limit: int,
    now: datetime,
    config: QueueConfig,
) -> list[ClaimedJob]:
    """Lease up to ``limit`` jobs to ``worker``.

    Order: manual priority, then the stage the worker ran last (fewer model switches), then
    FIFO by batch first-seen time and asset creation. After the first job the remaining slots
    are filled with the same stage so a worker gets homogeneous work. Server-side stages
    (``finalize``) are never handed out.
    """
    worker.capabilities = caps.model_dump(mode="json")
    worker.last_seen_at = now
    worker.status = "online"
    stages = [s.value for s in caps.claimable_stages]
    if not stages or await is_paused(session):
        return []
    prefer = worker.last_stage
    claimed: list[Job] = []
    for _ in range(limit):
        job = (
            (await session.execute(_next_job_statement(caps, stages, prefer, now)))
            .scalars()
            .first()
        )
        if job is None:
            break
        job.state = "leased"
        job.lease_owner = worker.id
        job.lease_expires_at = now + timedelta(seconds=config.lease_seconds)
        claimed.append(job)
        stages = [job.stage]  # batch the rest of this claim on the same stage
        prefer = job.stage
        await session.flush()
    if claimed:
        worker.last_stage = claimed[-1].stage
        worker.current_job_id = claimed[-1].id
    return [await _describe(session, job) for job in claimed]


async def _describe(session: AsyncSession, job: Job) -> ClaimedJob:
    asset = await session.get_one(Asset, job.asset_id)
    files = list(
        (await session.execute(select(AssetFile).where(AssetFile.asset_id == job.asset_id)))
        .scalars()
        .all()
    )
    results = (
        await session.execute(
            select(StageResult)
            .where(StageResult.asset_id == job.asset_id, StageResult.ok.is_(True))
            .order_by(StageResult.stage, StageResult.finished_at.desc(), StageResult.id.desc())
            .ext(distinct_on(StageResult.stage))
        )
    ).scalars()
    return ClaimedJob(job, asset, files, {r.stage: r.output for r in results})


async def _locked_job(session: AsyncSession, job_id: UUID) -> Job:
    job = (
        await session.execute(
            select(Job)
            .where(Job.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if job is None:
        raise JobNotFoundError(str(job_id))
    return job


def _require_owner(job: Job, worker: Worker | None) -> None:
    """``worker=None`` stands for the in-process server worker (leases without owner)."""
    owner = worker.id if worker else None
    if job.state != "leased" or job.lease_owner != owner:
        raise LeaseLostError(f"job {job.id} is not leased to {owner or 'the server'}")


async def heartbeat(
    session: AsyncSession, job_id: UUID, worker: Worker, now: datetime, config: QueueConfig
) -> datetime:
    """Extend the lease of ``job_id``; returns the new expiry.

    A lease that expired but was not reaped yet can still be extended by its owner.
    """
    job = await _locked_job(session, job_id)
    _require_owner(job, worker)
    job.lease_expires_at = now + timedelta(seconds=config.lease_seconds)
    worker.last_seen_at = now
    worker.status = "online"
    return job.lease_expires_at


async def release_job(session: AsyncSession, job_id: UUID, worker: Worker, now: datetime) -> None:
    """Give a job back without an attempt penalty (graceful worker shutdown)."""
    job = await _locked_job(session, job_id)
    _require_owner(job, worker)
    job.state = "queued"
    job.lease_owner = None
    job.lease_expires_at = None
    worker.current_job_id = None
    worker.last_seen_at = now


# --- results -----------------------------------------------------------------------------


async def submit_result(
    session: AsyncSession,
    job_id: UUID,
    worker: Worker | None,
    data: ResultData,
    now: datetime,
    config: QueueConfig,
) -> ResultOutcome:
    """Record a worker's result idempotently.

    * A job that is already finished (``done``/``failed``/``cancelled``) ignores the call.
    * Otherwise the worker must own the lease, else :class:`LeaseLostError`.
    * Success appends a ``stage_result`` and unblocks dependents; failure counts an attempt
      and requeues with backoff, or fails the job (and quarantines the asset) at the limit.
    """
    job = await _locked_job(session, job_id)
    if job.state in ("done", "failed", "cancelled"):
        return ResultOutcome(accepted=False, duplicate=True, job_state=job.state)
    _require_owner(job, worker)
    if worker is not None:
        worker.last_seen_at = now
        worker.current_job_id = None
    _append_result(session, job, worker.id if worker else None, data, now)
    if data.ok:
        await _apply_output(session, job, data.output)
        job.state = "done"
        job.lease_owner = None
        job.lease_expires_at = None
        job.last_error = None
        await session.flush()
        await _unblock_dependents(session, job.asset_id)
        await _refresh_batch_of_asset(session, job.asset_id, now)
        return ResultOutcome(accepted=True, duplicate=False, job_state="done")
    error = data.error or "worker reported an error"
    await _register_failure(session, job, error, now, config, retryable=data.retryable)
    return ResultOutcome(accepted=True, duplicate=False, job_state=job.state)


def _append_result(
    session: AsyncSession, job: Job, worker_id: UUID | None, data: ResultData, now: datetime
) -> None:
    session.add(
        StageResult(
            asset_id=job.asset_id,
            job_id=job.id,
            stage=job.stage,
            model_id=data.model_id,
            model_version=data.model_version,
            prompt_version=data.prompt_version,
            worker_id=worker_id,
            started_at=data.started_at,
            finished_at=now,
            duration_ms=data.duration_ms,
            ok=data.ok,
            output=data.output if data.ok else {},
            error=None if data.ok else data.error,
        )
    )


async def _register_failure(
    session: AsyncSession,
    job: Job,
    error: str,
    now: datetime,
    config: QueueConfig,
    *,
    retryable: bool = True,
) -> None:
    """Count one failed attempt: requeue with backoff, or fail the job at the limit."""
    job.attempts += 1
    job.last_error = error
    job.lease_owner = None
    job.lease_expires_at = None
    if retryable and job.attempts < job.max_attempts:
        job.state = "queued"
        job.not_before = now + config.backoff(job.attempts)
        return
    await _fail_job(session, job, error, now)


async def _fail_job(session: AsyncSession, job: Job, reason: str, now: datetime) -> None:
    """Fail ``job`` permanently and record the intent to quarantine its asset.

    The physical move into ``.rushbase/quarantine/`` is performed by the filing service
    (milestone M2); here the asset status and the audit trail carry the decision.
    """
    job.state = "failed"
    asset = await session.get_one(Asset, job.asset_id)
    asset.status = "quarantined"
    await session.execute(
        update(Job)
        .where(Job.asset_id == job.asset_id, Job.id != job.id, Job.state.in_(OPEN_STATES))
        .values(state="cancelled", lease_owner=None, lease_expires_at=None)
    )
    audit.record(
        session,
        actor_kind="system",
        action="asset.quarantine_intent",
        target_kind="asset",
        target_id=asset.id,
        details={
            "job_id": str(job.id),
            "stage": job.stage,
            "attempts": job.attempts,
            "reason": reason[:500],
            "move_to": ".rushbase/quarantine/ (performed by the filing service, M2)",
        },
    )
    await session.flush()
    await _refresh_batch_of_asset(session, job.asset_id, now)


async def _apply_output(session: AsyncSession, job: Job, output: dict[str, Any]) -> None:
    """Copy facts that live on their own columns out of a stage output."""
    if job.stage == Stage.HASH.value and isinstance(output.get("files"), dict):
        for rel_path, digest in output["files"].items():
            await session.execute(
                update(AssetFile)
                .where(AssetFile.asset_id == job.asset_id, AssetFile.rel_path == rel_path)
                .values(blake3=str(digest))
            )


async def _unblock_dependents(session: AsyncSession, asset_id: UUID) -> int:
    jobs = (await session.execute(select(Job).where(Job.asset_id == asset_id))).scalars().all()
    done = {j.id for j in jobs if j.state == "done"}
    unblocked = 0
    for job in jobs:
        if job.state == "blocked" and set(job.depends_on) <= done:
            job.state = "queued"
            unblocked += 1
    return unblocked


# --- reaper ------------------------------------------------------------------------------


async def reap_expired(session: AsyncSession, now: datetime, config: QueueConfig) -> ReapReport:
    """Return jobs whose lease expired to the queue (attempts + 1, exponential backoff).

    Jobs that reached ``max_attempts`` fail and their asset is marked ``quarantined``.
    """
    expired = (
        (
            await session.execute(
                select(Job)
                .where(Job.state == "leased", Job.lease_expires_at < now)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    requeued = failed = 0
    for job in expired:
        owner = job.lease_owner
        await _register_failure(session, job, f"lease expired (worker {owner})", now, config)
        if owner is not None:
            await session.execute(
                update(Worker)
                .where(Worker.id == owner, Worker.current_job_id == job.id)
                .values(current_job_id=None)
            )
        if job.state == "failed":
            failed += 1
        else:
            requeued += 1
    return ReapReport(requeued=requeued, failed=failed)


async def claim_server_job(
    session: AsyncSession, stages: Collection[Stage], now: datetime, config: QueueConfig
) -> Job | None:
    """Lease one job of ``stages`` to the in-process server worker (no worker row)."""
    if await is_paused(session):
        return None
    names = [s.value for s in stages]
    job = (await session.execute(_next_job_statement(None, names, None, now))).scalars().first()
    if job is not None:
        job.state = "leased"
        job.lease_owner = None
        job.lease_expires_at = now + timedelta(seconds=config.lease_seconds)
    return job


# --- server-side stage -------------------------------------------------------------------


async def run_server_stages(session: AsyncSession, now: datetime, limit: int = 100) -> int:
    """Complete ``finalize`` jobs, which the server executes itself.

    Milestone M1 has no filing yet: the job records that analysis is complete and moves the
    asset to ``review`` ("analysis done, awaiting filing"). M2 replaces this with the real
    filing step (rule evaluation, rename, sidecars, journal).
    """
    if await is_paused(session):
        return 0
    done = 0
    for _ in range(limit):
        job = (
            (await session.execute(_next_job_statement(None, [Stage.FINALIZE.value], None, now)))
            .scalars()
            .first()
        )
        if job is None:
            break
        _append_result(
            session,
            job,
            None,
            ResultData(ok=True, output={"filed": False, "reason": "filing arrives with M2"}),
            now,
        )
        job.state = "done"
        asset = await session.get_one(Asset, job.asset_id)
        if asset.status == "processing":
            asset.status = "review"
        await session.flush()
        await _refresh_batch_of_asset(session, job.asset_id, now)
        done += 1
    return done


# --- batch status ------------------------------------------------------------------------


async def refresh_batch_status(session: AsyncSession, batch_id: UUID, now: datetime) -> str:
    """Recompute ``batch.status`` from its files, jobs and assets; returns the new status."""
    batch = await session.get_one(Batch, batch_id)
    asset_ids = select(Asset.id).where(Asset.batch_id == batch_id)
    copying = await session.scalar(
        select(func.count())
        .select_from(InputFile)
        .where(InputFile.batch_id == batch_id, InputFile.state == "copying")
    )
    open_jobs = await session.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.asset_id.in_(asset_ids), Job.state.in_(OPEN_STATES))
    )
    quarantined = await session.scalar(
        select(func.count())
        .select_from(Asset)
        .where(Asset.batch_id == batch_id, Asset.status == "quarantined")
    )
    assets = await session.scalar(
        select(func.count()).select_from(Asset).where(Asset.batch_id == batch_id)
    )
    if copying:
        status = "scanning"
    elif open_jobs:
        status = "processing"
    elif quarantined:
        status = "error"
    elif assets:
        status = "done"
    else:
        status = "waiting"
    if status == "done" and batch.completed_at is None:
        batch.completed_at = now
    if status != "done":
        batch.completed_at = None
    batch.status = status
    return status


async def _refresh_batch_of_asset(session: AsyncSession, asset_id: UUID, now: datetime) -> None:
    batch_id = await session.scalar(select(Asset.batch_id).where(Asset.id == asset_id))
    if batch_id is not None:
        await refresh_batch_status(session, batch_id, now)


# --- reprocessing ------------------------------------------------------------------------


async def reprocess_stage(
    session: AsyncSession,
    stage: Stage,
    below_version: str,
    now: datetime,
    config: QueueConfig,
    batch_id: UUID | None = None,
    user_id: UUID | None = None,
) -> list[UUID]:
    """Re-enqueue ``stage`` for assets whose latest successful result is older than a version.

    Assets without an open job for the stage and not quarantined/trashed are considered.
    Results without a ``model_version`` count as the oldest possible version.
    Returns the ids of the assets that got a new job.
    """
    latest = (
        select(StageResult)
        .where(StageResult.stage == stage.value, StageResult.ok.is_(True))
        .order_by(StageResult.asset_id, StageResult.finished_at.desc(), StageResult.id.desc())
        .ext(distinct_on(StageResult.asset_id))
        .subquery()
    )
    stmt = (
        select(Asset, Batch, latest.c.model_version)
        .join(latest, latest.c.asset_id == Asset.id)
        .outerjoin(Batch, Batch.id == Asset.batch_id)
        .where(Asset.status.notin_(INACTIVE_ASSET_STATUSES))
        .order_by(Asset.id)
    )
    if batch_id is not None:
        stmt = stmt.where(Asset.batch_id == batch_id)
    open_assets = set(
        (
            await session.execute(
                select(Job.asset_id).where(Job.stage == stage.value, Job.state.in_(OPEN_STATES))
            )
        ).scalars()
    )
    touched: list[UUID] = []
    for asset, batch, model_version in (await session.execute(stmt)).all():
        if asset.id in open_assets or not version_lt(model_version or "0", below_version):
            continue
        session.add(
            Job(
                id=uuid7(),
                asset_id=asset.id,
                stage=stage.value,
                state="queued",
                depends_on=[],
                priority=batch.priority if batch else 0,
                enqueued_at=batch.first_seen_at if batch else now,
                max_attempts=config.max_attempts,
                requirements={},
            )
        )
        touched.append(asset.id)
    audit.record(
        session,
        actor_kind="user" if user_id else "system",
        actor_id=user_id,
        action="queue.reprocess",
        details={"stage": stage.value, "below_version": below_version, "assets": len(touched)},
    )
    return touched


# --- statistics --------------------------------------------------------------------------


async def backlog(session: AsyncSession) -> dict[str, dict[str, int]]:
    """Return job counts as ``{stage: {state: n}}``."""
    rows = await session.execute(
        select(Job.stage, Job.state, func.count()).group_by(Job.stage, Job.state)
    )
    result: dict[str, dict[str, int]] = {}
    for stage, state, count in rows:
        result.setdefault(stage, {})[state] = count
    return result
