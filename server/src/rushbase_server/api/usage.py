"""Usage feedback ("used in project"): Resolve reports, manual marks."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from rushbase_server.api.deps import (
    ActiveDep,
    DbDep,
    EditorDep,
    RuntimeDep,
    ViewerDep,
    WorkerDep,
    client_ip,
)
from rushbase_server.models import Asset
from rushbase_server.services import audit, usage

router = APIRouter(tags=["usage"])


class UsageItemIn(BaseModel):
    """One Rushbase clip of the project."""

    asset_id: UUID
    uses: int = Field(ge=0, le=1_000_000, description="Timeline uses; 0 = only in the pool.")


class UsageReportIn(BaseModel):
    """What a companion read from one Resolve project."""

    project_name: str = Field(min_length=1, max_length=300)
    machine: str = Field(default="", max_length=200)
    items: list[UsageItemIn] = Field(max_length=usage.MAX_REPORT_ENTRIES)


class UsageReportOut(BaseModel):
    """Counts of an accepted report."""

    recorded: int
    cleared: int
    unknown: int


class ProjectCount(BaseModel):
    """A project name and how many assets are used in it."""

    project_name: str
    assets: int


class ManualUsageIn(BaseModel):
    """Mark an asset as used in a project by hand."""

    project_name: str = Field(min_length=1, max_length=300)


async def _record(
    body: UsageReportIn,
    db: DbDep,
    runtime: RuntimeDep,
    actor: tuple[str, UUID],
    request: Request,
) -> UsageReportOut:
    result = await usage.report(
        db,
        project_name=body.project_name,
        machine=body.machine,
        entries=[usage.UsageEntry(i.asset_id, i.uses) for i in body.items],
        source="resolve",
        now=runtime.clock.now(),
    )
    audit.record(
        db,
        actor_kind=actor[0],
        actor_id=actor[1],
        action="usage.report",
        target_kind="project",
        target_id=body.project_name,
        details={"recorded": result.recorded, "cleared": result.cleared},
        ip=client_ip(request),
    )
    await db.commit()
    return UsageReportOut(recorded=result.recorded, cleared=result.cleared, unknown=result.unknown)


@router.post(
    "/api/v1/usage/report",
    response_model=UsageReportOut,
    summary="Report the Rushbase clips of a Resolve project (personal token or session)",
)
async def report_usage(
    body: UsageReportIn, editor: EditorDep, request: Request, runtime: RuntimeDep, db: DbDep
) -> UsageReportOut:
    """Upsert usage rows (source ``resolve``); entries with ``uses = 0`` clear the row."""
    return await _record(body, db, runtime, ("user", editor.user.id), request)


@router.post(
    "/api/v1/worker/usage",
    response_model=UsageReportOut,
    summary="Report the Rushbase clips of a Resolve project (worker key)",
)
async def report_usage_as_worker(
    body: UsageReportIn,
    worker: WorkerDep,
    _active: ActiveDep,
    request: Request,
    runtime: RuntimeDep,
    db: DbDep,
) -> UsageReportOut:
    """Same as the personal-token variant, for machines enrolled as workers only."""
    return await _record(body, db, runtime, ("worker", worker.id), request)


@router.get("/api/v1/usage/projects", response_model=list[ProjectCount], summary="Projects")
async def list_projects(_: ViewerDep, db: DbDep) -> list[ProjectCount]:
    """Projects any asset was used in, with asset counts."""
    return [ProjectCount(project_name=n, assets=c) for n, c in await usage.project_counts(db)]


@router.put(
    "/api/v1/assets/{asset_id}/usage",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark an asset as used in a project by hand",
)
async def mark_used(
    asset_id: UUID,
    body: ManualUsageIn,
    editor: EditorDep,
    request: Request,
    runtime: RuntimeDep,
    db: DbDep,
) -> None:
    """Add a ``manual`` usage row."""
    if await db.get(Asset, asset_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    await usage.report(
        db,
        project_name=body.project_name,
        machine="",
        entries=[usage.UsageEntry(asset_id, 1)],
        source="manual",
        now=runtime.clock.now(),
    )
    audit.record(
        db,
        actor_kind="user",
        actor_id=editor.user.id,
        action="usage.mark",
        target_kind="asset",
        target_id=asset_id,
        details={"project": body.project_name},
        ip=client_ip(request),
    )
    await db.commit()


@router.delete(
    "/api/v1/assets/{asset_id}/usage",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a usage mark",
)
async def unmark_used(
    asset_id: UUID,
    editor: EditorDep,
    request: Request,
    db: DbDep,
    project_name: Annotated[str, Query(min_length=1, max_length=300)],
    source: Annotated[str, Query(pattern="^(manual|resolve|claude)$")] = "manual",
) -> None:
    """Delete one usage row (a Resolve row reappears with the next sync while it is true)."""
    if not await usage.remove(db, asset_id, project_name, source):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "usage not found")
    audit.record(
        db,
        actor_kind="user",
        actor_id=editor.user.id,
        action="usage.unmark",
        target_kind="asset",
        target_id=asset_id,
        details={"project": project_name, "source": source},
        ip=client_ip(request),
    )
    await db.commit()
