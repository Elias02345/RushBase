"""First-run setup, login/logout, current user, personal API tokens and user management."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from rushbase_core import LibraryError, ServerLeaseHeldError
from rushbase_server.api.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    AdminDep,
    DbDep,
    PrincipalDep,
    RuntimeDep,
    client_ip,
)
from rushbase_server.db import upgrade_to_head
from rushbase_server.models import ApiToken, Library, Session, User
from rushbase_server.runtime import Runtime
from rushbase_server.services import audit, backup
from rushbase_server.services.security import (
    PERSONAL_TOKEN_PREFIX,
    WeakPasswordError,
    constant_time_equals,
    hash_password,
    hash_token,
    new_secret,
    new_token,
    verify_password,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["auth"])

Role = Literal["admin", "editor", "viewer"]
Locale = Literal["de", "en"]
SETUP_LOCK_KEY = 7_410_001


class UserOut(BaseModel):
    """A user as shown in the API (never includes the password hash)."""

    id: UUID
    username: str
    display_name: str
    role: Role
    locale: Locale
    disabled: bool
    last_login_at: datetime | None

    @classmethod
    def of(cls, user: User) -> "UserOut":
        """Convert a database user."""
        return cls.model_validate(user, from_attributes=True)


class LoginIn(BaseModel):
    """Credentials."""

    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1000)


class LoginOut(BaseModel):
    """Result of a successful login. The CSRF token is also set as a cookie."""

    user: UserOut
    csrf_token: str


class BackupOut(BaseModel):
    """A backup file offered for restore."""

    name: str
    size: int
    created_at: datetime
    kind: str


class SetupStatus(BaseModel):
    """What the setup wizard needs to know."""

    needs_setup: bool
    setup_token_required: bool = False
    library_configured: bool
    library_initialised: bool
    restore_available: bool
    backups: list[BackupOut]


class SetupIn(BaseModel):
    """First-run request: creates the admin and initialises or adopts the library."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1000)
    display_name: str = Field(default="", max_length=200)
    library_name: str = Field(default="Rushbase", min_length=1, max_length=200)
    locale: Locale = "de"
    setup_token: str = Field(default="", max_length=200)


def _check_setup_token(runtime: Runtime, request: Request, supplied: str) -> None:
    """Require the installer-generated setup code (when configured); failures are rate-limited."""
    expected = runtime.settings.setup_token
    if expected is None:
        return
    key = f"setup:{client_ip(request) or 'unknown'}"
    retry = runtime.limiter.retry_after(key)
    if retry:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many failed attempts",
            headers={"Retry-After": str(retry)},
        )
    if not constant_time_equals(supplied, expected.get_secret_value()):
        runtime.limiter.register_failure(key)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid setup token")


def _set_session_cookies(response: Response, runtime: RuntimeDep, token: str, csrf: str) -> None:
    max_age = int(runtime.settings.session_ttl_hours * 3600)
    secure = runtime.settings.use_secure_cookies
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE, csrf, max_age=max_age, httponly=False, samesite="lax", secure=secure, path="/"
    )


async def _create_session(
    request: Request, response: Response, runtime: RuntimeDep, db: DbDep, user: User
) -> str:
    token, csrf = new_secret(32), new_secret(24)
    now = runtime.clock.now()
    db.add(
        Session(
            user_id=user.id,
            token_hash=hash_token(token),
            csrf_token=csrf,
            expires_at=now + timedelta(hours=runtime.settings.session_ttl_hours),
            ip=client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:300],
        )
    )
    user.last_login_at = now
    _set_session_cookies(response, runtime, token, csrf)
    return csrf


@router.get("/setup", response_model=SetupStatus, summary="First-run status")
async def setup_status(runtime: RuntimeDep, db: DbDep) -> SetupStatus:
    """Report whether setup is needed and whether a backup can be restored instead."""
    has_user = bool(await db.scalar(select(func.count()).select_from(User)))
    root = runtime.binding.root
    backups = []
    if not has_user and root is not None:
        backups = backup.list_backups(backup.backup_dir(root))
    return SetupStatus(
        needs_setup=not has_user,
        setup_token_required=runtime.settings.setup_token is not None,
        library_configured=root is not None,
        library_initialised=runtime.binding.initialised(),
        restore_available=bool(backups),
        backups=[
            BackupOut(name=b.name, size=b.size, created_at=b.created_at, kind=b.kind)
            for b in backups
        ],
    )


@router.post(
    "/setup",
    response_model=LoginOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create the first admin and initialise (or adopt) the library",
)
async def setup(
    body: SetupIn, request: Request, response: Response, runtime: RuntimeDep, db: DbDep
) -> LoginOut:
    """Only works while no user exists. Logs the new admin in."""
    _check_setup_token(runtime, request, body.setup_token)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SETUP_LOCK_KEY})
    if await db.scalar(select(func.count()).select_from(User)):
        raise HTTPException(status.HTTP_409_CONFLICT, "setup is already complete")
    if runtime.binding.root is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "RUSHBASE_LIBRARY_ROOT is not configured")
    try:
        password_hash = await hash_password(body.password)
    except WeakPasswordError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    try:
        manifest = await runtime.binding.init_and_bind()
    except ServerLeaseHeldError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (LibraryError, OSError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"library not usable: {exc}") from exc
    library = await db.get(Library, manifest.library_id)
    if library is None:
        if await db.scalar(select(func.count()).select_from(Library)):
            raise HTTPException(status.HTTP_409_CONFLICT, "database belongs to a different library")
        db.add(
            Library(
                id=manifest.library_id,
                name=body.library_name,
                root_hint=None,
                schema_version=manifest.schema_version,
            )
        )
    user = User(
        username=body.username.strip(),
        display_name=body.display_name.strip() or body.username.strip(),
        password_hash=password_hash,
        role="admin",
        locale=body.locale,
    )
    db.add(user)
    await db.flush()
    audit.record(
        db,
        actor_kind="user",
        actor_id=user.id,
        action="setup.complete",
        target_kind="library",
        target_id=manifest.library_id,
        ip=client_ip(request),
    )
    csrf = await _create_session(request, response, runtime, db, user)
    await db.commit()
    return LoginOut(user=UserOut.of(user), csrf_token=csrf)


class RestoreIn(BaseModel):
    """Which backup of the share to restore."""

    backup_name: str = Field(min_length=1, max_length=300)
    setup_token: str = Field(default="", max_length=200)


class RestoreOut(BaseModel):
    """Result of a restore."""

    ok: bool


_restore_lock = asyncio.Lock()


@router.post(
    "/setup/restore",
    response_model=RestoreOut,
    summary="Restore a backup from the share instead of running the setup wizard",
)
async def restore_setup(body: RestoreIn, request: Request, runtime: RuntimeDep) -> RestoreOut:
    """Only works while the database is empty; restores, migrates and re-binds the library."""
    _check_setup_token(runtime, request, body.setup_token)
    root = runtime.binding.root
    if root is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "RUSHBASE_LIBRARY_ROOT is not configured")
    offered = {b.name: b for b in backup.list_backups(backup.backup_dir(root))}
    chosen = offered.get(body.backup_name)
    if chosen is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "backup not found")
    async with _restore_lock:
        try:
            await backup.restore_backup(
                runtime.engine, runtime.pg_tools, runtime.engine.url, chosen.path
            )
        except backup.BackupError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        await runtime.engine.dispose()
        await upgrade_to_head(runtime.settings)
        try:
            await runtime.binding.init_and_bind()
        except (ServerLeaseHeldError, LibraryError, OSError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, f"library not usable: {exc}") from exc
    logger.info("restored backup %s from %s", chosen.name, client_ip(request))
    return RestoreOut(ok=True)


@router.post("/auth/login", response_model=LoginOut, summary="Sign in")
async def login(
    body: LoginIn, request: Request, response: Response, runtime: RuntimeDep, db: DbDep
) -> LoginOut:
    """Verify credentials, rate-limited per client address and per user name."""
    ip = client_ip(request) or "unknown"
    keys = [f"ip:{ip}", f"user:{body.username.lower()}"]
    retry = max(runtime.limiter.retry_after(k) for k in keys)
    if retry:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many failed logins",
            headers={"Retry-After": str(retry)},
        )
    user = (
        await db.execute(select(User).where(User.username == body.username.strip()))
    ).scalar_one_or_none()
    valid = await verify_password(body.password, user.password_hash if user else None)
    if user is None or user.disabled or not valid:
        for key in keys:
            runtime.limiter.register_failure(key)
        audit.record(
            db,
            actor_kind="user",
            actor_id=user.id if user else None,
            action="auth.login_failed",
            details={"username": body.username[:64]},
            ip=ip,
        )
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    runtime.limiter.reset(f"user:{body.username.lower()}")
    csrf = await _create_session(request, response, runtime, db, user)
    audit.record(db, actor_kind="user", actor_id=user.id, action="auth.login", ip=ip)
    await db.commit()
    return LoginOut(user=UserOut.of(user), csrf_token=csrf)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Sign out")
async def logout(principal: PrincipalDep, request: Request, response: Response, db: DbDep) -> None:
    """Delete the current session and clear the cookies."""
    if principal.session is not None:
        await db.delete(principal.session)
    audit.record(
        db,
        actor_kind="user",
        actor_id=principal.user.id,
        action="auth.logout",
        ip=client_ip(request),
    )
    await db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.get("/auth/me", response_model=UserOut, summary="Current user")
async def me(principal: PrincipalDep) -> UserOut:
    """Return the signed-in user."""
    return UserOut.of(principal.user)


# --- personal API tokens ----------------------------------------------------------------


class TokenIn(BaseModel):
    """Request for a new personal API token."""

    name: str = Field(min_length=1, max_length=100)
    scopes: list[Literal["read", "write"]] = Field(
        default_factory=list,
        description="Empty: full access of the user's role. Only 'read': safe methods only.",
    )
    expires_in_days: int | None = Field(
        default=None, ge=1, le=3650, description="Lifetime in days; omitted: never expires."
    )


class TokenOut(BaseModel):
    """Token metadata (the secret is only shown once, in :class:`TokenCreated`)."""

    id: UUID
    name: str
    prefix: str
    scopes: list[str]
    last_used_at: datetime | None
    revoked_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


class TokenCreated(TokenOut):
    """A newly created token including its plaintext, shown exactly once."""

    token: str


@router.post(
    "/tokens", response_model=TokenCreated, status_code=status.HTTP_201_CREATED, tags=["tokens"]
)
async def create_token(
    body: TokenIn, principal: PrincipalDep, runtime: RuntimeDep, request: Request, db: DbDep
) -> TokenCreated:
    """Create a personal API token for the signed-in user (stored hashed)."""
    secret = new_token(PERSONAL_TOKEN_PREFIX)
    token = ApiToken(
        user_id=principal.user.id,
        kind="personal",
        name=body.name,
        token_hash=secret.token_hash,
        prefix=secret.prefix,
        scopes=body.scopes,
        expires_at=(
            runtime.clock.now() + timedelta(days=body.expires_in_days)
            if body.expires_in_days
            else None
        ),
    )
    db.add(token)
    await db.flush()
    audit.record(
        db,
        actor_kind="user",
        actor_id=principal.user.id,
        action="api_token.create",
        target_kind="api_token",
        target_id=token.id,
        ip=client_ip(request),
    )
    await db.commit()
    await db.refresh(token)
    return TokenCreated(
        **TokenOut.model_validate(token, from_attributes=True).model_dump(), token=secret.plaintext
    )


@router.get("/tokens", response_model=list[TokenOut], tags=["tokens"])
async def list_tokens(principal: PrincipalDep, db: DbDep) -> list[TokenOut]:
    """List the signed-in user's personal API tokens."""
    rows = await db.execute(
        select(ApiToken)
        .where(ApiToken.user_id == principal.user.id, ApiToken.kind == "personal")
        .order_by(ApiToken.created_at.desc())
    )
    return [TokenOut.model_validate(t, from_attributes=True) for t in rows.scalars()]


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["tokens"])
async def revoke_token(
    token_id: UUID, principal: PrincipalDep, runtime: RuntimeDep, request: Request, db: DbDep
) -> None:
    """Revoke one of the caller's tokens (admins may revoke any personal token)."""
    token = await db.get(ApiToken, token_id)
    owner_or_admin = token is not None and (
        token.user_id == principal.user.id or principal.user.role == "admin"
    )
    if token is None or token.kind != "personal" or not owner_or_admin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "token not found")
    token.revoked_at = runtime.clock.now()
    audit.record(
        db,
        actor_kind="user",
        actor_id=principal.user.id,
        action="api_token.revoke",
        target_kind="api_token",
        target_id=token.id,
        ip=client_ip(request),
    )
    await db.commit()


# --- user administration -----------------------------------------------------------------


class UserCreate(BaseModel):
    """Create a user (admin only)."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1000)
    display_name: str = Field(default="", max_length=200)
    role: Role = "viewer"
    locale: Locale = "de"


class UserUpdate(BaseModel):
    """Partial user update (admin only)."""

    role: Role | None = None
    disabled: bool | None = None
    password: Annotated[str | None, Field(max_length=1000)] = None
    display_name: Annotated[str | None, Field(max_length=200)] = None


@router.get("/admin/users", response_model=list[UserOut], tags=["admin"])
async def list_users(_: AdminDep, db: DbDep) -> list[UserOut]:
    """List all users."""
    rows = await db.execute(select(User).order_by(User.username))
    return [UserOut.of(u) for u in rows.scalars()]


@router.post(
    "/admin/users", response_model=UserOut, status_code=status.HTTP_201_CREATED, tags=["admin"]
)
async def create_user(body: UserCreate, admin: AdminDep, request: Request, db: DbDep) -> UserOut:
    """Create a user with the given role."""
    try:
        password_hash = await hash_password(body.password)
    except WeakPasswordError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    user = User(
        username=body.username.strip(),
        display_name=body.display_name.strip() or body.username.strip(),
        password_hash=password_hash,
        role=body.role,
        locale=body.locale,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "username already exists") from exc
    audit.record(
        db,
        actor_kind="user",
        actor_id=admin.user.id,
        action="user.create",
        target_kind="user",
        target_id=user.id,
        details={"role": body.role},
        ip=client_ip(request),
    )
    await db.commit()
    return UserOut.of(user)


@router.patch("/admin/users/{user_id}", response_model=UserOut, tags=["admin"])
async def update_user(
    user_id: UUID, body: UserUpdate, admin: AdminDep, request: Request, db: DbDep
) -> UserOut:
    """Change role, disabled flag, display name or password of a user."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    details: dict[str, object] = {}
    if body.role is not None:
        if user.id == admin.user.id and body.role != "admin":
            raise HTTPException(status.HTTP_409_CONFLICT, "cannot demote yourself")
        user.role = body.role
        details["role"] = body.role
    if body.disabled is not None:
        if user.id == admin.user.id and body.disabled:
            raise HTTPException(status.HTTP_409_CONFLICT, "cannot disable yourself")
        user.disabled = body.disabled
        details["disabled"] = body.disabled
    if body.display_name:
        user.display_name = body.display_name
    if body.password is not None:
        try:
            user.password_hash = await hash_password(body.password)
        except WeakPasswordError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        details["password_changed"] = True
        await db.execute(delete(Session).where(Session.user_id == user.id))
    audit.record(
        db,
        actor_kind="user",
        actor_id=admin.user.id,
        action="user.update",
        target_kind="user",
        target_id=user.id,
        details=details,
        ip=client_ip(request),
    )
    await db.commit()
    return UserOut.of(user)
