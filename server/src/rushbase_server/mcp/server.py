"""The Rushbase MCP server: tools from the shared contract, resources, audit and rate limits."""

import base64
import hashlib
import inspect
import json
import logging
import time
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.streamable_http_manager import (
    StreamableHTTPASGIApp,
    StreamableHTTPSessionManager,
)
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import Field
from starlette.types import ASGIApp

from rushbase_core import mcp_tools as spec
from rushbase_server import get_version
from rushbase_server.mcp import tools
from rushbase_server.mcp.gateway import CallAuth, McpGateway, current_call
from rushbase_server.mcp.output import ToolFailure, ToolOutput, clamp_images
from rushbase_server.models import McpAudit, OAuthClient, User
from rushbase_server.runtime import Runtime

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Rushbase is a self-hosted media library (video, photo, audio). Start with `library_stats` "
    "or `list_facets`, then `search_media`. Results are paginated: pass `next_cursor` back. "
    "Use `get_asset` for details and `get_keyframes` for pictures. Writes (annotate, taxonomy, "
    "rules, needs, reprocess) are logged; tags you set carry source `claude`, human edits win. "
    "No tool deletes or moves files."
)


def transport_security(public_url: str | None) -> TransportSecuritySettings:
    """Host/Origin allowlist: the public host plus localhost."""
    hosts = ["localhost:*", "127.0.0.1:*", "[::1]:*"]
    origins = ["http://localhost:*", "http://127.0.0.1:*", "http://[::1]:*"]
    if public_url:
        host = public_url.split("://", 1)[1]
        hosts += [host, f"{host}:*"]
        origins.append(public_url)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
    )


def _signature(tool: spec.ToolSpec) -> inspect.Signature:
    params = []
    for name, info in tool.model.model_fields.items():
        default: Any = (
            inspect.Parameter.empty
            if info.is_required()
            else info.get_default(call_default_factory=True)
        )
        params.append(
            inspect.Parameter(
                name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default,
                annotation=Annotated[
                    info.annotation, *info.metadata, Field(description=info.description)
                ],
            )
        )
    return inspect.Signature(params, return_annotation=CallToolResult)


async def _audit(
    runtime: Runtime,
    call: CallAuth,
    tool: str,
    kwargs: dict[str, Any],
    started: float,
    chars: int,
    error: str | None,
) -> None:
    args = json.dumps(kwargs, sort_keys=True, default=lambda o: o.model_dump(mode="json"))
    try:
        async with runtime.session_factory() as db:
            db.add(
                McpAudit(
                    client_id=call.client_pk,
                    client_name=call.client_name,
                    user_id=call.user_id,
                    tool=tool,
                    args_hash=hashlib.sha256(args.encode()).hexdigest(),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    result_chars=chars,
                    ok=error is None,
                    error=error,
                )
            )
            client = await db.get(OAuthClient, call.client_pk)
            if client is not None:
                client.last_used_at = runtime.clock.now()
            await db.commit()
    except Exception:
        logger.warning("could not write the MCP audit entry", exc_info=True)


def _authenticate(runtime: Runtime, scope: str) -> CallAuth:
    call = current_call.get()
    if call is None:
        raise ToolError("not authenticated")
    if scope not in call.scopes:
        raise ToolError(f"the OAuth scope '{scope}' was not granted; reconnect and allow it")
    limit = call.rate_limit or runtime.settings.mcp_rate_limit_per_minute
    if not runtime.buckets.allow(f"mcp:{call.client_pk}", limit, runtime.clock.now()):
        raise ToolError(
            f"rate limit exceeded ({limit} calls/minute); retry in "
            f"{runtime.buckets.retry_after(limit)} s"
        )
    return call


async def _run(runtime: Runtime, call: CallAuth, name: str, params: Any) -> ToolOutput:
    async with runtime.session_factory() as db:
        user = await db.get(User, call.user_id)
        if user is None or user.disabled:
            raise ToolFailure("the connected account is no longer active")
        ctx = tools.Ctx(runtime, db, user, call.client_name)
        return await tools.HANDLERS[name](ctx, params)


def _make_tool(tool: spec.ToolSpec, get_runtime: Callable[[], Runtime]) -> Callable[..., Any]:
    async def call_tool(**kwargs: Any) -> CallToolResult:
        runtime = get_runtime()
        call = _authenticate(runtime, tool.scope)
        started = time.perf_counter()
        error: str | None = None
        chars = 0
        try:
            output = await _run(runtime, call, tool.name, tool.model.model_validate(kwargs))
            text = tools.render(output)
            chars = len(text)
            content: list[Any] = [TextContent(text=text)]
            content += [
                ImageContent(data=base64.b64encode(data).decode(), mime_type="image/jpeg")
                for _, data in clamp_images(output.images)
            ]
            return CallToolResult(content=content)
        except ToolFailure as exc:
            error = str(exc)
            raise ToolError(error) from exc
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            await _audit(runtime, call, tool.name, kwargs, started, chars, error)

    call_tool.__name__ = tool.name
    call_tool.__doc__ = tool.description
    call_tool.__signature__ = _signature(tool)  # type: ignore[attr-defined]
    return call_tool


def _register_resources(mcp: MCPServer[Any], get_runtime: Callable[[], Runtime]) -> None:
    async def read(scope: str, handler: str, params: spec.Strict) -> str:
        runtime = get_runtime()
        call = _authenticate(runtime, scope)
        started = time.perf_counter()
        output = await _run(runtime, call, handler, params)
        text = tools.render(output)
        await _audit(runtime, call, f"resource:{handler}", {}, started, len(text), None)
        return text

    @mcp.resource("rushbase://library/overview", mime_type="application/json")
    async def overview() -> str:
        """Asset counts, storage and capture range of the library."""
        return await read("read", "library_stats", spec.LibraryStats())

    @mcp.resource("rushbase://taxonomy", mime_type="application/json")
    async def taxonomy() -> str:
        """The controlled vocabulary (fields, values, synonyms)."""
        return await read("read", "taxonomy_list", spec.TaxonomyList())

    @mcp.resource("rushbase://rules", mime_type="application/json")
    async def rules() -> str:
        """The active filing rule and recent versions."""
        return await read("read", "rules_get", spec.RulesGet())


def build_mcp(
    get_runtime: Callable[[], Runtime], public_url: str | None
) -> tuple[ASGIApp, StreamableHTTPSessionManager]:
    """Create the server; returns the authenticated ASGI endpoint and the session manager.

    The session manager's ``run()`` must be active while the app serves (see the app lifespan).
    """
    mcp = MCPServer("Rushbase", version=get_version(), instructions=INSTRUCTIONS)
    for tool in spec.TOOLS:
        mcp.add_tool(
            _make_tool(tool, get_runtime),
            name=tool.name,
            description=tool.description,
            annotations=ToolAnnotations(
                read_only_hint=not tool.writes, destructive_hint=False, open_world_hint=False
            ),
            structured_output=False,
        )
    _register_resources(mcp, get_runtime)
    mcp.streamable_http_app(
        stateless_http=True, json_response=True, transport_security=transport_security(public_url)
    )
    manager = mcp.session_manager
    return McpGateway(get_runtime, StreamableHTTPASGIApp(manager)), manager
