"""OAuth 2.1 authorization server logic: clients, codes, tokens (opaque, stored hashed)."""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core import uuid7
from rushbase_core.mcp_tools import ALL_SCOPES
from rushbase_server.models import OAuthClient, OAuthCode, OAuthToken, User
from rushbase_server.oauth import cimd, redirects
from rushbase_server.services.security import constant_time_equals, hash_token

DEFAULT_SCOPES = ("read",)
CODE_TTL = timedelta(minutes=5)
MAX_DCR_CLIENTS = 500
UNUSED_CLIENT_TTL = timedelta(days=1)
ACCESS_PREFIX = "rba"
REFRESH_PREFIX = "rbr"
CODE_PREFIX = "rbc"


class OAuthError(Exception):
    """An OAuth protocol error (RFC 6749 section 5.2)."""

    def __init__(self, code: str, description: str, status: int = 400) -> None:
        super().__init__(description)
        self.code = code
        self.description = description
        self.status = status


@dataclass(frozen=True)
class IssuedTokens:
    """Token endpoint response."""

    access_token: str
    refresh_token: str
    expires_in: int
    scope: str

    def body(self) -> dict[str, Any]:
        """JSON body of the token response."""
        return {
            "access_token": self.access_token,
            "token_type": "Bearer",
            "expires_in": self.expires_in,
            "refresh_token": self.refresh_token,
            "scope": self.scope,
        }


@dataclass(frozen=True)
class VerifiedAccess:
    """A valid access token with its owner."""

    token_id: UUID
    client: OAuthClient
    user: User
    scopes: tuple[str, ...]
    resource: str
    expires_at: datetime


@dataclass(frozen=True)
class OAuthConfig:
    """Per-request configuration derived from settings and admin overrides."""

    resource: str
    access_ttl: timedelta
    refresh_ttl: timedelta
    extra_redirects: tuple[str, ...] = ()


# --- scopes and PKCE -------------------------------------------------------------------------


def parse_scopes(raw: str | None, allowed: tuple[str, ...] = ALL_SCOPES) -> list[str]:
    """Split a scope string; unknown scopes are an ``invalid_scope`` error, none means ``read``."""
    scopes = list(dict.fromkeys((raw or "").split()))
    unknown = [s for s in scopes if s not in allowed]
    if unknown:
        raise OAuthError("invalid_scope", f"unknown scope: {unknown[0]}")
    return scopes or list(DEFAULT_SCOPES)


def pkce_challenge(verifier: str) -> str:
    """S256 transform of a code verifier."""
    digest = hashlib.sha256(verifier.encode("ascii", "ignore")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _verifier_valid(verifier: str) -> bool:
    return 43 <= len(verifier) <= 128 and all(
        c.isalnum() or c in "-._~" for c in verifier
    )


# --- clients ---------------------------------------------------------------------------------


async def register_client(
    db: AsyncSession, body: dict[str, Any], extra: tuple[str, ...], now: datetime
) -> OAuthClient:
    """Dynamic client registration (RFC 7591) for public clients."""
    uris = body.get("redirect_uris")
    if not (isinstance(uris, list) and uris and all(isinstance(u, str) for u in uris)):
        raise OAuthError("invalid_redirect_uri", "redirect_uris is required")
    if len(uris) > 10:
        raise OAuthError("invalid_redirect_uri", "too many redirect_uris")
    for uri in uris:
        if not redirects.is_allowed(uri, extra):
            raise OAuthError("invalid_redirect_uri", f"redirect URI not allowed: {uri[:200]}")
    if body.get("token_endpoint_auth_method", "none") != "none":
        raise OAuthError("invalid_client_metadata", "only token_endpoint_auth_method none")
    grants = body.get("grant_types", ["authorization_code", "refresh_token"])
    if not isinstance(grants, list) or not set(grants) <= {"authorization_code", "refresh_token"}:
        raise OAuthError("invalid_client_metadata", "unsupported grant_types")
    scopes = parse_scopes(body.get("scope"))
    name = body.get("client_name")
    await db.execute(
        delete(OAuthClient).where(
            OAuthClient.kind == "dcr",
            OAuthClient.created_at < now - UNUSED_CLIENT_TTL,
            OAuthClient.last_used_at.is_(None),
            ~OAuthClient.id.in_(select(OAuthToken.client_id)),
        )
    )
    count = len((await db.execute(select(OAuthClient.id).where(OAuthClient.kind == "dcr"))).all())
    if count >= MAX_DCR_CLIENTS:
        raise OAuthError("invalid_client_metadata", "too many registered clients", 429)
    client = OAuthClient(
        id=uuid7(),
        client_id=f"rbclient_{secrets.token_urlsafe(18)}",
        kind="dcr",
        name=(name.strip() if isinstance(name, str) and name.strip() else "MCP client")[:120],
        redirect_uris=list(dict.fromkeys(uris)),
        scopes=scopes,
    )
    db.add(client)
    await db.flush()
    return client


async def load_client(
    db: AsyncSession,
    client_id: str,
    now: datetime,
    *,
    resolver: cimd.Resolver = cimd.system_resolver,
    transport: Any = None,
) -> OAuthClient:
    """Return the client for ``client_id`` (CIMD documents are fetched and cached on demand)."""
    client = (
        await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
    ).scalar_one_or_none()
    if client is not None and client.revoked_at is not None:
        raise OAuthError("invalid_client", "client was revoked", 401)
    if not cimd.is_cimd_client_id(client_id):
        if client is None:
            raise OAuthError("invalid_client", "unknown client", 401)
        return client
    if client is not None and client.metadata_expires_at and client.metadata_expires_at > now:
        return client
    try:
        meta = await cimd.fetch_client_metadata(
            client_id,
            resolver=resolver,
            transport=transport,
            etag=client.metadata_etag if client else None,
        )
    except cimd.CimdError as exc:
        if client is not None:  # keep serving the last good document during an outage
            return client
        raise OAuthError("invalid_client", f"client metadata: {exc}", 401) from exc
    expires = now + timedelta(seconds=meta.cache_seconds)
    if meta.not_modified and client is not None:
        client.metadata_expires_at = expires
        return client
    if client is None:
        client = OAuthClient(id=uuid7(), client_id=client_id, kind="cimd")
        db.add(client)
    client.name = meta.name
    client.redirect_uris = meta.redirect_uris
    client.scopes = meta.scopes
    client.metadata_expires_at = expires
    client.metadata_etag = meta.etag
    await db.flush()
    return client


def check_redirect(client: OAuthClient, redirect_uri: str, extra: tuple[str, ...]) -> None:
    """The redirect URI must be registered for the client and on the allowlist."""
    if not redirects.is_allowed(redirect_uri, extra) or not redirects.matches(
        redirect_uri, client.redirect_uris
    ):
        raise OAuthError("invalid_request", "redirect_uri is not registered for this client")


def check_resource(resource: str | None, config: OAuthConfig) -> str:
    """RFC 8707: the resource must be this server's MCP URL (absent means the default)."""
    if resource is not None and resource.rstrip("/") != config.resource:
        raise OAuthError("invalid_target", "resource must be the MCP server URL")
    return config.resource


# --- authorization codes ---------------------------------------------------------------------


async def issue_code(
    db: AsyncSession,
    client: OAuthClient,
    user: User,
    *,
    redirect_uri: str,
    scopes: list[str],
    resource: str,
    code_challenge: str,
    now: datetime,
) -> str:
    """Create a single-use authorization code (5 minutes)."""
    await db.execute(delete(OAuthCode).where(OAuthCode.expires_at < now - timedelta(hours=1)))
    code = f"{CODE_PREFIX}_{secrets.token_urlsafe(32)}"
    db.add(
        OAuthCode(
            id=uuid7(),
            code_hash=hash_token(code),
            client_id=client.id,
            user_id=user.id,
            redirect_uri=redirect_uri,
            scopes=scopes,
            resource=resource,
            code_challenge=code_challenge,
            expires_at=now + CODE_TTL,
        )
    )
    return code


async def _mint(
    db: AsyncSession,
    config: OAuthConfig,
    *,
    family: UUID,
    client_pk: UUID,
    user_id: UUID,
    scopes: list[str],
    resource: str,
    now: datetime,
) -> IssuedTokens:
    access = f"{ACCESS_PREFIX}_{secrets.token_urlsafe(32)}"
    refresh = f"{REFRESH_PREFIX}_{secrets.token_urlsafe(32)}"
    for kind, token, ttl in (
        ("access", access, config.access_ttl),
        ("refresh", refresh, config.refresh_ttl),
    ):
        db.add(
            OAuthToken(
                id=uuid7(),
                family_id=family,
                kind=kind,
                token_hash=hash_token(token),
                client_id=client_pk,
                user_id=user_id,
                scopes=scopes,
                resource=resource,
                expires_at=now + ttl,
            )
        )
    return IssuedTokens(access, refresh, int(config.access_ttl.total_seconds()), " ".join(scopes))


async def revoke_family(db: AsyncSession, family: UUID, now: datetime) -> None:
    """Revoke every token of a grant."""
    await db.execute(
        update(OAuthToken)
        .where(OAuthToken.family_id == family, OAuthToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def exchange_code(
    db: AsyncSession,
    config: OAuthConfig,
    client: OAuthClient,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    resource: str | None,
    now: datetime,
) -> IssuedTokens:
    """``authorization_code`` grant with mandatory PKCE (S256)."""
    row = (
        await db.execute(
            select(OAuthCode).where(OAuthCode.code_hash == hash_token(code)).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.client_id != client.id:
        raise OAuthError("invalid_grant", "authorization code is invalid")
    if row.used_at is not None:  # replay: burn everything that came from this code
        await revoke_family(db, row.id, now)
        await db.commit()
        raise OAuthError("invalid_grant", "authorization code was already used")
    row.used_at = now
    if row.expires_at <= now:
        await db.commit()
        raise OAuthError("invalid_grant", "authorization code expired")
    if row.redirect_uri != redirect_uri:
        await db.commit()
        raise OAuthError("invalid_grant", "redirect_uri does not match the authorization request")
    if not _verifier_valid(code_verifier) or not constant_time_equals(
        pkce_challenge(code_verifier), row.code_challenge
    ):
        await db.commit()
        raise OAuthError("invalid_grant", "PKCE verification failed")
    if resource is not None and resource.rstrip("/") != row.resource:
        await db.commit()
        raise OAuthError("invalid_target", "resource does not match the authorization request")
    user = await db.get(User, row.user_id)
    if user is None or user.disabled:
        await db.commit()
        raise OAuthError("invalid_grant", "user is not available")
    tokens = await _mint(
        db,
        config,
        family=row.id,
        client_pk=client.id,
        user_id=user.id,
        scopes=list(row.scopes),
        resource=row.resource,
        now=now,
    )
    client.last_used_at = now
    await db.commit()
    return tokens


async def refresh(
    db: AsyncSession,
    config: OAuthConfig,
    client: OAuthClient,
    *,
    refresh_token: str,
    resource: str | None,
    scope: str | None,
    now: datetime,
) -> IssuedTokens:
    """``refresh_token`` grant: rotation, sliding expiry and reuse detection."""
    row = (
        await db.execute(
            select(OAuthToken)
            .where(OAuthToken.token_hash == hash_token(refresh_token), OAuthToken.kind == "refresh")
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.client_id != client.id:
        raise OAuthError("invalid_grant", "refresh token is invalid")
    if row.used_at is not None:  # a rotated token came back: assume theft, end the grant
        await revoke_family(db, row.family_id, now)
        await db.commit()
        raise OAuthError("invalid_grant", "refresh token was already used")
    if row.revoked_at is not None or row.expires_at <= now:
        raise OAuthError("invalid_grant", "refresh token expired or revoked")
    if resource is not None and resource.rstrip("/") != row.resource:
        raise OAuthError("invalid_target", "resource does not match the grant")
    granted = list(row.scopes)
    scopes = granted
    if scope:
        requested = parse_scopes(scope)
        if not set(requested) <= set(granted):
            raise OAuthError("invalid_scope", "cannot widen the granted scopes")
        scopes = requested
    user = await db.get(User, row.user_id)
    if user is None or user.disabled:
        raise OAuthError("invalid_grant", "user is not available")
    row.used_at = now
    await db.execute(
        update(OAuthToken)
        .where(
            OAuthToken.family_id == row.family_id,
            OAuthToken.kind == "access",
            OAuthToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    tokens = await _mint(
        db,
        config,
        family=row.family_id,
        client_pk=client.id,
        user_id=user.id,
        scopes=scopes,
        resource=row.resource,
        now=now,
    )
    client.last_used_at = now
    await db.commit()
    return tokens


async def revoke_token(db: AsyncSession, token: str, now: datetime) -> None:
    """RFC 7009: revoke the grant a token belongs to (unknown tokens are ignored)."""
    row = (
        await db.execute(select(OAuthToken).where(OAuthToken.token_hash == hash_token(token)))
    ).scalar_one_or_none()
    if row is not None:
        await revoke_family(db, row.family_id, now)
        await db.commit()


async def revoke_client(db: AsyncSession, client_pk: UUID, now: datetime) -> bool:
    """Admin action: revoke the client and all its tokens."""
    client = await db.get(OAuthClient, client_pk)
    if client is None:
        return False
    client.revoked_at = now
    await db.execute(
        update(OAuthToken)
        .where(OAuthToken.client_id == client_pk, OAuthToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    return True


# --- resource server side --------------------------------------------------------------------


async def verify_access(
    db: AsyncSession, token: str, resource: str, now: datetime
) -> VerifiedAccess | None:
    """Return the owner of a valid access token for ``resource``, else ``None``."""
    if not token.startswith(f"{ACCESS_PREFIX}_"):
        return None
    row = (
        await db.execute(
            select(OAuthToken).where(
                OAuthToken.token_hash == hash_token(token), OAuthToken.kind == "access"
            )
        )
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        return None
    if row.resource.rstrip("/") != resource.rstrip("/"):
        return None
    client = await db.get(OAuthClient, row.client_id)
    user = await db.get(User, row.user_id)
    if client is None or client.revoked_at is not None or user is None or user.disabled:
        return None
    return VerifiedAccess(row.id, client, user, tuple(row.scopes), row.resource, row.expires_at)


def issuer_host(url: str) -> str:
    """Host part of the public URL (transport security allowlist)."""
    return urlsplit(url).netloc
