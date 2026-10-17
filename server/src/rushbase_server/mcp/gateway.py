"""ASGI gateway in front of the MCP transport: bearer verification, audience, 401 challenge."""

import json
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from starlette.types import ASGIApp, Receive, Scope, Send

from rushbase_server.oauth import service
from rushbase_server.runtime import Runtime
from rushbase_server.services import audit


@dataclass(frozen=True)
class CallAuth:
    """The verified caller of the current MCP request."""

    client_pk: UUID
    client_name: str
    rate_limit: int | None
    user_id: UUID
    scopes: frozenset[str]


current_call: ContextVar[CallAuth | None] = ContextVar("rushbase_mcp_call", default=None)


def _bearer(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return str(token.strip())
    return None


async def _respond(
    send: Send, status: int, body: dict[str, str], headers: list[tuple[bytes, bytes]] | None = None
) -> None:
    raw = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(raw)).encode()),
                (b"cache-control", b"no-store"),
                *(headers or []),
            ],
        }
    )
    await send({"type": "http.response.body", "body": raw})


AUDITED_401_PER_MINUTE = 6


class McpGateway:
    """Authenticates ``/mcp`` requests and hands them to the SDK's Streamable HTTP app."""

    def __init__(self, get_runtime: Callable[[], Runtime], inner: ASGIApp) -> None:
        self._get_runtime = get_runtime
        self._inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._inner(scope, receive, send)
            return
        runtime = self._get_runtime()
        settings = runtime.settings
        if settings.public_url is None or settings.mcp_resource is None:
            await _respond(
                send,
                503,
                {
                    "error": "mcp_not_configured",
                    "error_description": "RUSHBASE_PUBLIC_URL is not set; MCP is disabled",
                },
            )
            return
        challenge = (
            f'Bearer resource_metadata="{settings.public_url}'
            f'/.well-known/oauth-protected-resource/mcp", scope="read"'
        )
        token = _bearer(scope)
        verified = None
        if token is not None:
            async with runtime.session_factory() as db:
                verified = await service.verify_access(
                    db, token, settings.mcp_resource, runtime.clock.now()
                )
        if verified is None:
            await self._audit_rejection(runtime, scope, token is not None)
            header = challenge + (', error="invalid_token"' if token is not None else "")
            await _respond(
                send,
                401,
                {"error": "invalid_token", "error_description": "valid bearer token required"},
                [(b"www-authenticate", header.encode())],
            )
            return
        call = CallAuth(
            verified.client.id,
            verified.client.name,
            verified.client.rate_limit_per_minute,
            verified.user.id,
            frozenset(verified.scopes),
        )
        reset = current_call.set(call)
        try:
            await self._inner(scope, receive, send)
        finally:
            current_call.reset(reset)

    @staticmethod
    async def _audit_rejection(runtime: Runtime, scope: Scope, had_token: bool) -> None:
        """Record rejected MCP requests, rate-limited so a flood cannot grow the audit table."""
        if not runtime.buckets.allow("audit:mcp-401", AUDITED_401_PER_MINUTE, runtime.clock.now()):
            return
        client = scope.get("client")
        async with runtime.session_factory() as db:
            audit.record(
                db,
                actor_kind="system",
                action="mcp.unauthorized",
                details={"had_token": had_token},
                ip=str(client[0]) if client else None,
            )
            await db.commit()
