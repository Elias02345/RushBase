"""OAuth 2.1 authorization server endpoints, metadata documents and the consent page."""

import secrets
from datetime import timedelta
from typing import Annotated, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select

from rushbase_core.mcp_tools import ALL_SCOPES, SCOPES
from rushbase_server.api.deps import SESSION_COOKIE, DbDep, RuntimeDep, client_ip
from rushbase_server.models import OAuthClient, Session, User
from rushbase_server.oauth import pages, redirects, service
from rushbase_server.oauth.service import OAuthConfig, OAuthError
from rushbase_server.runtime import Runtime
from rushbase_server.services import audit
from rushbase_server.services.security import (
    constant_time_equals,
    hash_token,
    verify_password,
)
from rushbase_server.services.settings_store import OAUTH_EXTRA_REDIRECTS_KEY, get_setting

router = APIRouter(tags=["oauth"])
CSRF_COOKIE = "rb_oauth_csrf"
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
PAGE_HEADERS = {
    **NO_STORE,
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
    "frame-ancestors 'none'; form-action 'self' https: http:; base-uri 'none'",
    "Referrer-Policy": "no-referrer",
}
PARAMS = (
    "response_type",
    "client_id",
    "redirect_uri",
    "scope",
    "state",
    "code_challenge",
    "code_challenge_method",
    "resource",
)
ROLES_MAY_GRANT = ("admin", "editor")
REGISTER_PER_MINUTE = 20
LOGIN_PER_MINUTE = 30


async def oauth_config(runtime: Runtime, db: Any) -> OAuthConfig | None:
    """The OAuth configuration, ``None`` while ``RUSHBASE_PUBLIC_URL`` is unset."""
    resource = runtime.settings.mcp_resource
    if resource is None:
        return None
    extra = await get_setting(db, OAUTH_EXTRA_REDIRECTS_KEY, [])
    return OAuthConfig(
        resource=resource,
        access_ttl=timedelta(seconds=runtime.settings.mcp_access_ttl_seconds),
        refresh_ttl=timedelta(days=runtime.settings.mcp_refresh_ttl_days),
        extra_redirects=tuple(str(u) for u in extra),
    )


def _json(
    body: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={**NO_STORE, **(headers or {})})


def _error(exc: OAuthError) -> JSONResponse:
    headers = {"WWW-Authenticate": 'Bearer error="invalid_client"'} if exc.status == 401 else {}
    return _json({"error": exc.code, "error_description": exc.description}, exc.status, headers)


def _unconfigured() -> JSONResponse:
    return _json(
        {
            "error": "temporarily_unavailable",
            "error_description": "RUSHBASE_PUBLIC_URL is not configured; MCP is disabled",
        },
        503,
    )


# --- discovery -------------------------------------------------------------------------------


def authorization_server_metadata(issuer: str) -> dict[str, Any]:
    """RFC 8414 metadata document."""
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "registration_endpoint": f"{issuer}/oauth/register",
        "revocation_endpoint": f"{issuer}/oauth/revoke",
        "scopes_supported": list(ALL_SCOPES),
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
        "service_documentation": f"{issuer}/docs/mcp",
    }


@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
@router.get("/.well-known/oauth-authorization-server/mcp", include_in_schema=False)
async def as_metadata(runtime: RuntimeDep) -> JSONResponse:
    """Authorization server metadata (RFC 8414), also at the path-suffixed location."""
    if runtime.settings.public_url is None:
        return _unconfigured()
    return _json(authorization_server_metadata(runtime.settings.public_url))


@router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
@router.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
async def protected_resource_metadata(runtime: RuntimeDep) -> JSONResponse:
    """Protected resource metadata (RFC 9728); ``resource`` equals the MCP URL exactly."""
    settings = runtime.settings
    if settings.public_url is None or settings.mcp_resource is None:
        return _unconfigured()
    return _json(
        {
            "resource": settings.mcp_resource,
            "authorization_servers": [settings.public_url],
            "scopes_supported": list(SCOPES),
            "bearer_methods_supported": ["header"],
            "resource_name": "Rushbase",
        }
    )


# --- registration ----------------------------------------------------------------------------


@router.post("/oauth/register", include_in_schema=False)
async def register(request: Request, runtime: RuntimeDep, db: DbDep) -> JSONResponse:
    """Dynamic client registration (RFC 7591) for public clients."""
    config = await oauth_config(runtime, db)
    if config is None:
        return _unconfigured()
    now = runtime.clock.now()
    if not runtime.buckets.allow(f"register:{client_ip(request)}", REGISTER_PER_MINUTE, now):
        return _json({"error": "temporarily_unavailable"}, 429, {"Retry-After": "60"})
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
    except ValueError:
        return _json(
            {"error": "invalid_client_metadata", "error_description": "JSON object expected"}, 400
        )
    try:
        client = await service.register_client(db, body, config.extra_redirects, now)
    except OAuthError as exc:
        return _error(exc)
    audit.record(
        db,
        actor_kind="system",
        action="oauth.client_registered",
        target_kind="oauth_client",
        target_id=client.id,
        details={"name": client.name, "redirect_uris": client.redirect_uris},
        ip=client_ip(request),
    )
    await db.commit()
    return _json(
        {
            "client_id": client.client_id,
            "client_id_issued_at": int(now.timestamp()),
            "client_name": client.name,
            "redirect_uris": client.redirect_uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": " ".join(client.scopes),
        },
        201,
    )


# --- authorization endpoint ------------------------------------------------------------------


def _lang(request: Request, user: User | None = None) -> str:
    if user is not None:
        return user.locale if user.locale in pages.STRINGS else "de"
    header = request.headers.get("accept-language", "")
    return "en" if header.lower().startswith("en") else "de"


def _html(content: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(content, status_code=status, headers=PAGE_HEADERS)


def _redirect_back(
    redirect_uri: str, issuer: str, state: str | None, **params: str
) -> RedirectResponse:
    parts = urlsplit(redirect_uri)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query += list(params.items())
    if state:
        query.append(("state", state))
    query.append(("iss", issuer))
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    return RedirectResponse(url, status_code=303, headers=NO_STORE)


async def _session_user(request: Request, runtime: Runtime, db: Any) -> User | None:
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    session = (
        await db.execute(select(Session).where(Session.token_hash == hash_token(cookie)))
    ).scalar_one_or_none()
    if session is None or session.expires_at <= runtime.clock.now():
        return None
    user = await db.get(User, session.user_id)
    return None if user is None or user.disabled else user


async def _validate(
    db: Any, runtime: Runtime, config: OAuthConfig, params: dict[str, str]
) -> tuple[OAuthClient, str, list[str]]:
    """Validate client, redirect URI and scopes; failures here are shown, never redirected."""
    client_id, redirect_uri = params.get("client_id", ""), params.get("redirect_uri", "")
    if not client_id or not redirect_uri:
        raise OAuthError("invalid_request", "client_id and redirect_uri are required")
    client = await service.load_client(db, client_id, runtime.clock.now())
    service.check_redirect(client, redirect_uri, config.extra_redirects)
    return client, redirect_uri, service.parse_scopes(params.get("scope"))


def _check_request(params: dict[str, str], config: OAuthConfig) -> list[str]:
    """Errors reported to the client by redirect; returns the scopes."""
    if params.get("response_type") != "code":
        raise OAuthError("unsupported_response_type", "response_type must be code")
    if params.get("code_challenge_method") != "S256" or not params.get("code_challenge"):
        raise OAuthError("invalid_request", "PKCE with code_challenge_method S256 is required")
    service.check_resource(params.get("resource"), config)
    return service.parse_scopes(params.get("scope"))


def _new_csrf() -> str:
    return secrets.token_urlsafe(24)


async def _consent_response(
    request: Request,
    runtime: Runtime,
    client: OAuthClient,
    redirect_uri: str,
    scopes: list[str],
    params: dict[str, str],
    user: User | None,
    message: str | None = None,
    status: int = 200,
) -> HTMLResponse:
    csrf = _new_csrf()
    lang = _lang(request, user)
    page = pages.consent_page(
        lang,
        client_name=client.name,
        redirect_host=redirects.host_of(redirect_uri),
        scopes=scopes,
        fields={k: params.get(k, "") for k in PARAMS if params.get(k)},
        csrf=csrf,
        signed_in_as=user.display_name or user.username if user else None,
        message=message,
    )
    response = _html(page, status)
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=900,
        httponly=True,
        samesite="strict",
        secure=runtime.settings.use_secure_cookies,
        path="/oauth",
    )
    return response


@router.get("/oauth/authorize", include_in_schema=False)
async def authorize_page(request: Request, runtime: RuntimeDep, db: DbDep) -> Response:
    """Consent page: shows client, redirect host and scopes; login with Rushbase credentials."""
    config = await oauth_config(runtime, db)
    lang = _lang(request)
    if config is None:
        return _html(pages.error_page(lang, pages.STRINGS[lang]["unavailable"]), 503)
    params = dict(request.query_params)
    try:
        client, redirect_uri, _ = await _validate(db, runtime, config, params)
    except OAuthError as exc:
        return _html(pages.error_page(lang, exc.description), 400)
    issuer = runtime.settings.public_url or ""
    try:
        scopes = _check_request(params, config)
    except OAuthError as exc:
        return _redirect_back(
            redirect_uri,
            issuer,
            params.get("state"),
            error=exc.code,
            error_description=exc.description,
        )
    await db.commit()  # persist a freshly fetched CIMD client
    user = await _session_user(request, runtime, db)
    return await _consent_response(request, runtime, client, redirect_uri, scopes, params, user)


@router.post("/oauth/authorize", include_in_schema=False)
async def authorize_submit(request: Request, runtime: RuntimeDep, db: DbDep) -> Response:
    """Handle the consent form: authenticate, check the role, issue the code."""
    config = await oauth_config(runtime, db)
    lang = _lang(request)
    if config is None:
        return _html(pages.error_page(lang, pages.STRINGS[lang]["unavailable"]), 503)
    form = await request.form()
    values = {k: str(v) for k, v in form.items() if isinstance(v, str)}
    sent, cookie = values.get("csrf", ""), request.cookies.get(CSRF_COOKIE, "")
    if not (sent and cookie and constant_time_equals(sent, cookie)):
        return _html(pages.error_page(lang, "CSRF check failed. Reload the page."), 403)
    try:
        client, redirect_uri, _ = await _validate(db, runtime, config, values)
    except OAuthError as exc:
        return _html(pages.error_page(lang, exc.description), 400)
    issuer = runtime.settings.public_url or ""
    state = values.get("state")
    try:
        scopes = _check_request(values, config)
    except OAuthError as exc:
        return _redirect_back(
            redirect_uri, issuer, state, error=exc.code, error_description=exc.description
        )
    if values.get("decision") != "allow":
        return _redirect_back(redirect_uri, issuer, state, error="access_denied")

    now = runtime.clock.now()
    user = await _session_user(request, runtime, db)
    t = pages.STRINGS[_lang(request, user)]
    if user is None:
        ip = client_ip(request) or "unknown"
        username = values.get("username", "").strip()
        keys = [f"ip:{ip}", f"user:{username.lower()}"]
        if any(runtime.limiter.retry_after(k) for k in keys) or not runtime.buckets.allow(
            f"oauth-login:{ip}", LOGIN_PER_MINUTE, now
        ):
            return await _consent_response(
                request, runtime, client, redirect_uri, scopes, values, None, t["too_many"], 429
            )
        candidate = (
            await db.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        valid = await verify_password(
            values.get("password", ""), candidate.password_hash if candidate else None
        )
        if candidate is None or candidate.disabled or not valid:
            for key in keys:
                runtime.limiter.register_failure(key)
            audit.record(
                db,
                actor_kind="user",
                actor_id=candidate.id if candidate else None,
                action="oauth.login_failed",
                details={"username": username[:64], "client": client.name},
                ip=ip,
            )
            await db.commit()
            return await _consent_response(
                request, runtime, client, redirect_uri, scopes, values, None, t["bad_login"], 401
            )
        runtime.limiter.reset(f"user:{username.lower()}")
        user = candidate
    if user.role not in ROLES_MAY_GRANT:
        return await _consent_response(
            request, runtime, client, redirect_uri, scopes, values, user, t["forbidden"], 403
        )
    code = await service.issue_code(
        db,
        client,
        user,
        redirect_uri=redirect_uri,
        scopes=scopes,
        resource=config.resource,
        code_challenge=values["code_challenge"],
        now=now,
    )
    audit.record(
        db,
        actor_kind="user",
        actor_id=user.id,
        action="oauth.authorized",
        target_kind="oauth_client",
        target_id=client.id,
        details={"client": client.name, "scopes": scopes},
        ip=client_ip(request),
    )
    await db.commit()
    response = _redirect_back(redirect_uri, issuer, state, code=code)
    response.delete_cookie(CSRF_COOKIE, path="/oauth")
    return response


# --- token endpoint --------------------------------------------------------------------------

FormStr = Annotated[str | None, Form()]


@router.post("/oauth/token", include_in_schema=False)
async def token(
    runtime: RuntimeDep,
    db: DbDep,
    grant_type: FormStr = None,
    client_id: FormStr = None,
    code: FormStr = None,
    redirect_uri: FormStr = None,
    code_verifier: FormStr = None,
    refresh_token: FormStr = None,
    resource: FormStr = None,
    scope: FormStr = None,
) -> JSONResponse:
    """Token endpoint: ``authorization_code`` (PKCE) and ``refresh_token`` (rotating)."""
    config = await oauth_config(runtime, db)
    if config is None:
        return _unconfigured()
    now = runtime.clock.now()
    try:
        if not client_id:
            raise OAuthError("invalid_client", "client_id is required", 401)
        client = await service.load_client(db, client_id, now)
        if not runtime.buckets.allow(f"token:{client.id}", 60, now):
            return _json({"error": "temporarily_unavailable"}, 429, {"Retry-After": "5"})
        if grant_type == "authorization_code":
            if not (code and redirect_uri and code_verifier):
                raise OAuthError(
                    "invalid_request", "code, redirect_uri and code_verifier are required"
                )
            tokens = await service.exchange_code(
                db,
                config,
                client,
                code=code,
                redirect_uri=redirect_uri,
                code_verifier=code_verifier,
                resource=resource,
                now=now,
            )
        elif grant_type == "refresh_token":
            if not refresh_token:
                raise OAuthError("invalid_request", "refresh_token is required")
            tokens = await service.refresh(
                db,
                config,
                client,
                refresh_token=refresh_token,
                resource=resource,
                scope=scope,
                now=now,
            )
        else:
            raise OAuthError(
                "unsupported_grant_type", "grant_type must be authorization_code or refresh_token"
            )
    except OAuthError as exc:
        await db.rollback()
        return _error(exc)
    return _json(tokens.body())


@router.post("/oauth/revoke", include_in_schema=False)
async def revoke(runtime: RuntimeDep, db: DbDep, token: FormStr = None) -> Response:
    """RFC 7009 revocation: always answers 200."""
    if token:
        await service.revoke_token(db, token, runtime.clock.now())
    return Response(status_code=200, headers=NO_STORE)
