"""FastAPI dependencies: database session, authentication, CSRF and role checks."""

from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.models import ApiToken, Session, User, Worker
from rushbase_server.runtime import Runtime
from rushbase_server.services.security import (
    PERSONAL_TOKEN_PREFIX,
    WORKER_TOKEN_PREFIX,
    constant_time_equals,
    hash_token,
)

SESSION_COOKIE = "rb_session"
CSRF_COOKIE = "rb_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}
TOKEN_TOUCH_INTERVAL = timedelta(seconds=60)


def get_runtime(request: Request) -> Runtime:
    """Return the :class:`Runtime` attached to the application."""
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]


async def get_db(runtime: RuntimeDep) -> AsyncIterator[AsyncSession]:
    """Yield a session. Handlers commit explicitly; an uncommitted session rolls back."""
    async with runtime.session_factory() as session:
        yield session


DbDep = Annotated[AsyncSession, Depends(get_db)]


def client_ip(request: Request) -> str | None:
    """Best-effort client address (the direct peer; proxies are configured at the proxy)."""
    return request.client.host if request.client else None


@dataclass(frozen=True)
class Principal:
    """The authenticated caller of a user-facing endpoint."""

    user: User
    session: Session | None  # None when authenticated by a personal API token
    token: ApiToken | None


def _unauthorized(detail: str = "authentication required") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


async def current_principal(request: Request, runtime: RuntimeDep, db: DbDep) -> Principal:
    """Authenticate by session cookie (with CSRF on unsafe methods) or personal API token."""
    now = runtime.clock.now()
    bearer = _bearer(request)
    if bearer is not None:
        if not bearer.startswith(f"{PERSONAL_TOKEN_PREFIX}_"):
            raise _unauthorized("invalid token")
        token = (
            await db.execute(
                select(ApiToken).where(
                    ApiToken.token_hash == hash_token(bearer),
                    ApiToken.kind == "personal",
                    ApiToken.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        user = await db.get(User, token.user_id) if token and token.user_id else None
        if token is None or user is None or user.disabled:
            raise _unauthorized("invalid token")
        if token.scopes and "write" not in token.scopes and request.method not in SAFE_METHODS:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "token scope 'read' allows reading only")
        if token.last_used_at is None or now - token.last_used_at > TOKEN_TOUCH_INTERVAL:
            token.last_used_at = now
            await db.commit()
        return Principal(user, None, token)

    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        raise _unauthorized()
    session = (
        await db.execute(select(Session).where(Session.token_hash == hash_token(cookie)))
    ).scalar_one_or_none()
    if session is None or session.expires_at <= now:
        raise _unauthorized("session expired")
    user = await db.get(User, session.user_id)
    if user is None or user.disabled:
        raise _unauthorized()
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        cookie_value = request.cookies.get(CSRF_COOKIE, "")
        if not (
            sent
            and constant_time_equals(sent, session.csrf_token)
            and constant_time_equals(sent, cookie_value)
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")
    return Principal(user, session, None)


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def require_role(minimum: str) -> Callable[[Principal], Coroutine[Any, Any, Principal]]:
    """Build a dependency that requires at least role ``minimum`` (viewer < editor < admin)."""

    async def check(principal: PrincipalDep) -> Principal:
        if ROLE_RANK[principal.user.role] < ROLE_RANK[minimum]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"{minimum} role required")
        return principal

    return check


ViewerDep = Annotated[Principal, Depends(require_role("viewer"))]
EditorDep = Annotated[Principal, Depends(require_role("editor"))]
AdminDep = Annotated[Principal, Depends(require_role("admin"))]


async def current_worker(request: Request, runtime: RuntimeDep, db: DbDep) -> Worker:
    """Authenticate a worker by its bearer API key."""
    bearer = _bearer(request)
    if bearer is None or not bearer.startswith(f"{WORKER_TOKEN_PREFIX}_"):
        raise _unauthorized("worker API key required")
    token = (
        await db.execute(
            select(ApiToken).where(
                ApiToken.token_hash == hash_token(bearer),
                ApiToken.kind == "worker",
                ApiToken.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    worker = await db.get(Worker, token.worker_id) if token and token.worker_id else None
    if token is None or worker is None:
        raise _unauthorized("invalid API key")
    now = runtime.clock.now()
    if token.last_used_at is None or now - token.last_used_at > TOKEN_TOUCH_INTERVAL:
        token.last_used_at = now
    return worker


WorkerDep = Annotated[Worker, Depends(current_worker)]


def require_active(runtime: RuntimeDep) -> None:
    """Reject worker traffic while this server does not hold the library lease."""
    if runtime.binding.root is not None and not runtime.binding.held:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "server is not the active coordinator"
        )


ActiveDep = Annotated[None, Depends(require_active)]
