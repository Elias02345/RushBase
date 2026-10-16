"""Administration of the MCP connector: status, connected OAuth clients, audit log."""

from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import distinct, func, select

from rushbase_core.mcp_tools import TOOLS
from rushbase_server.api.deps import AdminDep, DbDep, RuntimeDep, client_ip
from rushbase_server.models import McpAudit, OAuthClient, OAuthToken
from rushbase_server.oauth import service
from rushbase_server.services import audit
from rushbase_server.services.settings_store import (
    OAUTH_EXTRA_REDIRECTS_KEY,
    get_setting,
    set_setting,
)

router = APIRouter(prefix="/api/v1/admin/mcp", tags=["admin"])


class ClientOut(BaseModel):
    """A connected OAuth client."""

    id: UUID
    name: str
    kind: str
    client_id: str
    scopes: list[str]
    grants: int
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class McpStatus(BaseModel):
    """Connector status and settings."""

    enabled: bool
    public_url: str | None
    mcp_url: str | None
    warnings: list[str]
    rate_limit_per_minute: int
    extra_redirect_uris: list[str]
    tools: int
    clients: list[ClientOut]


class RedirectsIn(BaseModel):
    """Admin-configured extra redirect URIs (exact match, https or loopback)."""

    uris: list[str] = Field(max_length=20)


class AuditOut(BaseModel):
    """One MCP call."""

    id: UUID
    at: datetime
    client_name: str
    client_id: UUID | None
    tool: str
    duration_ms: int
    result_chars: int
    ok: bool
    error: str | None
    args_hash: str


class AuditPage(BaseModel):
    """A page of audit entries and the filter choices."""

    items: list[AuditOut]
    next_offset: int | None
    tools: list[str]


@router.get("", response_model=McpStatus, summary="MCP connector status")
async def mcp_status(_: AdminDep, runtime: RuntimeDep, db: DbDep) -> McpStatus:
    """Public URL, connector URL, connected clients and settings."""
    settings = runtime.settings
    clients = (
        (await db.execute(select(OAuthClient).order_by(OAuthClient.created_at.desc())))
        .scalars()
        .all()
    )
    now = runtime.clock.now()
    grant_rows = await db.execute(
        select(OAuthToken.client_id, func.count(distinct(OAuthToken.family_id)))
        .where(OAuthToken.revoked_at.is_(None), OAuthToken.expires_at > now)
        .group_by(OAuthToken.client_id)
    )
    grants = {cid: int(n) for cid, n in grant_rows.all()}
    scopes: dict[UUID, set[str]] = {}
    for client_id, granted in await db.execute(
        select(OAuthToken.client_id, OAuthToken.scopes).where(
            OAuthToken.revoked_at.is_(None), OAuthToken.expires_at > now
        )
    ):
        scopes.setdefault(client_id, set()).update(granted)
    return McpStatus(
        enabled=settings.public_url is not None,
        public_url=settings.public_url,
        mcp_url=settings.mcp_resource,
        warnings=[] if settings.public_url else ["mcp_public_url_missing"],
        rate_limit_per_minute=settings.mcp_rate_limit_per_minute,
        extra_redirect_uris=[str(u) for u in await get_setting(db, OAUTH_EXTRA_REDIRECTS_KEY, [])],
        tools=len(TOOLS),
        clients=[
            ClientOut(
                id=c.id,
                name=c.name,
                kind=c.kind,
                client_id=c.client_id,
                scopes=sorted(scopes.get(c.id, set())),
                grants=int(grants.get(c.id, 0)),
                created_at=c.created_at,
                last_used_at=c.last_used_at,
                revoked_at=c.revoked_at,
            )
            for c in clients
        ],
    )


@router.post("/clients/{client_pk}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_client(
    client_pk: UUID, admin: AdminDep, request: Request, runtime: RuntimeDep, db: DbDep
) -> None:
    """Revoke the client and every token it holds."""
    if not await service.revoke_client(db, client_pk, runtime.clock.now()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "client not found")
    audit.record(
        db,
        actor_kind="user",
        actor_id=admin.user.id,
        action="oauth.client_revoked",
        target_kind="oauth_client",
        target_id=client_pk,
        ip=client_ip(request),
    )
    await db.commit()


@router.put("/redirect-uris", response_model=list[str])
async def put_redirect_uris(body: RedirectsIn, admin: AdminDep, db: DbDep) -> list[str]:
    """Replace the extra redirect URIs (clients still have to register them)."""
    cleaned: list[str] = []
    for uri in body.uris:
        parts = urlsplit(uri.strip())
        if parts.scheme != "https" or not parts.hostname or parts.fragment:
            raise HTTPException(422, f"{uri!r} must be an https URL without fragment")
        cleaned.append(uri.strip())
    result = list(dict.fromkeys(cleaned))
    await set_setting(db, OAUTH_EXTRA_REDIRECTS_KEY, result, admin.user.id)
    await db.commit()
    return result


@router.get("/audit", response_model=AuditPage, summary="MCP call log")
async def mcp_audit(
    _: AdminDep,
    db: DbDep,
    client_id: UUID | None = None,
    tool: Annotated[str | None, Query(max_length=80)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    """Newest first; filter by client and tool."""
    stmt = select(McpAudit).order_by(McpAudit.at.desc(), McpAudit.id.desc())
    if client_id:
        stmt = stmt.where(McpAudit.client_id == client_id)
    if tool:
        stmt = stmt.where(McpAudit.tool == tool)
    rows = (await db.execute(stmt.offset(offset).limit(limit + 1))).scalars().all()
    tools = (await db.execute(select(McpAudit.tool).distinct().order_by(McpAudit.tool))).scalars()
    return AuditPage(
        items=[
            AuditOut(
                id=r.id,
                at=r.at,
                client_name=r.client_name,
                client_id=r.client_id,
                tool=r.tool,
                duration_ms=r.duration_ms,
                result_chars=r.result_chars,
                ok=r.ok,
                error=r.error,
                args_hash=r.args_hash,
            )
            for r in rows[:limit]
        ],
        next_offset=offset + limit if len(rows) > limit else None,
        tools=list(tools),
    )
