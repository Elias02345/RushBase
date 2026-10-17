"""Append-only audit log for security-relevant actions, and the read side of the admin viewer."""

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.models import AuditLog

MAX_PAGE = 200


def record(
    session: AsyncSession,
    *,
    actor_kind: str,
    action: str,
    actor_id: UUID | None = None,
    target_kind: str | None = None,
    target_id: str | UUID | None = None,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Add an audit entry to ``session`` (committed with the caller's transaction).

    ``details`` must never contain secrets (passwords, tokens).
    """
    session.add(
        AuditLog(
            actor_kind=actor_kind,
            actor_id=actor_id,
            action=action,
            target_kind=target_kind,
            target_id=str(target_id) if target_id is not None else None,
            details=details or {},
            ip=ip,
        )
    )


class InvalidCursorError(ValueError):
    """The pagination cursor is malformed."""


@dataclass(frozen=True)
class AuditFilters:
    """Filters of the audit viewer (all optional)."""

    actor_kind: str | None = None
    actor_id: UUID | None = None
    action: str | None = None
    """Exact action, or a prefix when it ends with ``*`` (``queue.*``)."""
    target_kind: str | None = None
    target_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None


def encode_cursor(entry: AuditLog) -> str:
    """Opaque cursor for ``(at, id)`` of the last row of a page."""
    raw = json.dumps([entry.at.isoformat(), str(entry.id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Inverse of :func:`encode_cursor`."""
    try:
        at, entry_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        return datetime.fromisoformat(at), UUID(entry_id)
    except (ValueError, TypeError) as exc:
        raise InvalidCursorError("invalid cursor") from exc


async def page(
    session: AsyncSession, filters: AuditFilters, cursor: str | None, limit: int
) -> tuple[list[AuditLog], str | None]:
    """Newest-first page of entries and the cursor of the next page (keyset on ``at, id``)."""
    stmt = select(AuditLog)
    for column, value in (
        (AuditLog.actor_kind, filters.actor_kind),
        (AuditLog.actor_id, filters.actor_id),
        (AuditLog.target_kind, filters.target_kind),
        (AuditLog.target_id, filters.target_id),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    if filters.action:
        if filters.action.endswith("*"):
            stmt = stmt.where(AuditLog.action.startswith(filters.action[:-1], autoescape=True))
        else:
            stmt = stmt.where(AuditLog.action == filters.action)
    if filters.since:
        stmt = stmt.where(AuditLog.at >= filters.since)
    if filters.until:
        stmt = stmt.where(AuditLog.at < filters.until)
    if cursor:
        at, entry_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(AuditLog.at, AuditLog.id) < tuple_(at, entry_id))
    rows = list(
        (
            await session.execute(
                stmt.order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit + 1)
            )
        ).scalars()
    )
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, (encode_cursor(rows[-1]) if more else None)
