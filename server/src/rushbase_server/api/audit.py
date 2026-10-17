"""Audit-log viewer for administrators (read-only, keyset pagination)."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.api.deps import AdminDep, DbDep, RuntimeDep
from rushbase_server.models import AuditLog, User, Worker
from rushbase_server.services import audit

router = APIRouter(prefix="/api/v1/admin/audit", tags=["audit"])


class AuditEntryOut(BaseModel):
    """One audit entry; ``actor_name`` resolves users and workers (``None`` when gone)."""

    id: UUID
    at: datetime
    actor_kind: str
    actor_id: UUID | None
    actor_name: str | None
    action: str
    target_kind: str | None
    target_id: str | None
    details: dict[str, Any]
    ip: str | None


class AuditPage(BaseModel):
    """A page of entries, newest first."""

    items: list[AuditEntryOut]
    next_cursor: str | None


async def _names(db: AsyncSession, rows: list[AuditLog]) -> dict[UUID, str]:
    user_ids = {r.actor_id for r in rows if r.actor_kind == "user" and r.actor_id}
    worker_ids = {r.actor_id for r in rows if r.actor_kind == "worker" and r.actor_id}
    names: dict[UUID, str] = {}
    if user_ids:
        found = await db.execute(select(User.id, User.username).where(User.id.in_(user_ids)))
        names |= {i: str(n) for i, n in found}
    if worker_ids:
        found = await db.execute(select(Worker.id, Worker.name).where(Worker.id.in_(worker_ids)))
        names |= {i: str(n) for i, n in found}
    return names


@router.get("", response_model=AuditPage, summary="Audit log")
async def list_audit(
    _: AdminDep,
    db: DbDep,
    actor_kind: str | None = None,
    actor_id: UUID | None = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    target_kind: str | None = None,
    target_id: Annotated[str | None, Query(max_length=200)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=audit.MAX_PAGE)] = 50,
) -> AuditPage:
    """Filter the log by actor, action (`prefix*` allowed), target and time range."""
    filters = audit.AuditFilters(actor_kind, actor_id, action, target_kind, target_id, since, until)
    try:
        rows, next_cursor = await audit.page(db, filters, cursor, limit)
    except audit.InvalidCursorError as exc:
        raise HTTPException(422, str(exc)) from exc
    names = await _names(db, rows)
    return AuditPage(
        items=[
            AuditEntryOut(
                id=r.id,
                at=r.at,
                actor_kind=r.actor_kind,
                actor_id=r.actor_id,
                actor_name=names.get(r.actor_id) if r.actor_id else None,
                action=r.action,
                target_kind=r.target_kind,
                target_id=r.target_id,
                details=r.details,
                ip=r.ip,
            )
            for r in rows
        ],
        next_cursor=next_cursor,
    )


@router.get("/actions", response_model=list[str], summary="Distinct actions in the log")
async def list_actions(_: AdminDep, runtime: RuntimeDep) -> list[str]:
    """Action names for the viewer's filter (cached; the log can be large)."""

    async def distinct(session: AsyncSession) -> list[str]:
        rows = await session.execute(select(AuditLog.action).distinct().order_by(AuditLog.action))
        return list(rows.scalars())

    return await runtime.cached("audit-actions", distinct)
