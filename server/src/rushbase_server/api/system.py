"""Operations endpoints: health, status and optional Prometheus metrics."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server import get_version
from rushbase_server.api.deps import DbDep, RuntimeDep, ViewerDep, WorkerDep
from rushbase_server.db import database_reachable
from rushbase_server.models import User
from rushbase_server.runtime import Runtime
from rushbase_server.services import stats

router = APIRouter()


class HealthResponse(BaseModel):
    """Result of the health probe.

    ``library_mounted`` / ``lease_held`` are ``null`` while no library is bound yet (a fresh
    install before the setup wizard ran); that state is healthy and shows ``setup_required``.
    """

    status: Literal["ok", "degraded"]
    version: str
    database: bool
    library_mounted: bool | None = None
    lease_held: bool | None = None
    setup_required: bool | None = None
    warnings: list[str] = []
    """Non-fatal configuration problems, e.g. ``mcp_public_url_missing``."""


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
    tags=["system"],
    summary="Liveness, database, library mount and server lease",
)
async def health(runtime: RuntimeDep, response: Response) -> HealthResponse:
    """503 when the database is down, the library is unreadable or the lease was lost."""
    database = await database_reachable(runtime.engine)
    binding = runtime.binding
    bound = binding.manifest is not None
    mounted = binding.mounted if bound else None
    held = binding.held if bound else None
    setup_required: bool | None = None
    if database:
        async with runtime.session_factory() as session:
            setup_required = not await session.scalar(select(func.count()).select_from(User))
    healthy = database and (not bound or (binding.mounted and binding.held))
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if healthy else "degraded",
        version=get_version(),
        database=database,
        library_mounted=mounted,
        lease_held=held,
        setup_required=setup_required,
        warnings=[] if runtime.settings.public_url else ["mcp_public_url_missing"],
    )


class StageBacklogOut(BaseModel):
    """Pending work of one stage."""

    jobs: dict[str, int]
    pending_jobs: int
    pending_files: int
    pending_bytes: int
    pending_duration_ms: int
    duration_unknown_jobs: int


class StatusOut(BaseModel):
    """Backlog and system counters. Available with or without workers online."""

    version: str
    queue_paused: bool
    stages: dict[str, StageBacklogOut]
    assets: dict[str, int]
    input_files: dict[str, int]
    batches: dict[str, int]
    workers_online: int
    workers_total: int


@router.get("/api/v1/status", response_model=StatusOut, tags=["system"])
async def system_status(_: ViewerDep, runtime: RuntimeDep, db: DbDep) -> StatusOut:
    """Backlog per stage (jobs, files, bytes, media duration), assets, inputs and workers."""
    return await _status(runtime, db)


@router.get("/api/v1/worker/status", response_model=StatusOut, tags=["worker"])
async def worker_status(_: WorkerDep, runtime: RuntimeDep, db: DbDep) -> StatusOut:
    """The same backlog snapshot for enrolled workers (shown when a processing PC starts)."""
    return await _status(runtime, db)


async def _status(runtime: Runtime, db: AsyncSession) -> StatusOut:
    snapshot = await stats.system_status(
        db, runtime.clock.now(), runtime.settings.worker_online_seconds
    )
    return StatusOut(
        version=get_version(),
        queue_paused=snapshot.queue_paused,
        stages={
            name: StageBacklogOut(**vars(backlog)) for name, backlog in snapshot.stages.items()
        },
        assets=snapshot.assets,
        input_files=snapshot.input_files,
        batches=snapshot.batches,
        workers_online=snapshot.workers_online,
        workers_total=snapshot.workers_total,
    )


@router.get("/metrics", include_in_schema=False)
async def metrics(runtime: RuntimeDep, db: DbDep) -> Response:
    """Prometheus text exposition; 404 unless ``RUSHBASE_METRICS_ENABLED`` is set."""
    if not runtime.settings.metrics_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    snapshot = await stats.system_status(
        db, runtime.clock.now(), runtime.settings.worker_online_seconds
    )
    lines = [
        "# TYPE rushbase_jobs gauge",
        "# TYPE rushbase_pending_bytes gauge",
        "# TYPE rushbase_assets gauge",
        "# TYPE rushbase_workers_online gauge",
        "# TYPE rushbase_queue_paused gauge",
    ]
    for stage, backlog in sorted(snapshot.stages.items()):
        for state, count in sorted(backlog.jobs.items()):
            lines.append(f'rushbase_jobs{{stage="{stage}",state="{state}"}} {count}')
        lines.append(f'rushbase_pending_bytes{{stage="{stage}"}} {backlog.pending_bytes}')
    lines += [f'rushbase_assets{{status="{k}"}} {v}' for k, v in sorted(snapshot.assets.items())]
    lines.append(f"rushbase_workers_online {snapshot.workers_online}")
    lines.append(f"rushbase_queue_paused {int(snapshot.queue_paused)}")
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
