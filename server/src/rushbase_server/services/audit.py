"""Append-only audit log for security-relevant actions."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.models import AuditLog


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
