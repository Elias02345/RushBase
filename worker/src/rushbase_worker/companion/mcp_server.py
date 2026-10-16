"""Local MCP server: the companion's CLI functions as tools (never deletes or moves anything)."""

import functools
import hmac
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from rushbase_worker.companion import service
from rushbase_worker.companion.api import UserApiError
from rushbase_worker.companion.clips import local_path
from rushbase_worker.companion.processing import ProcessController
from rushbase_worker.companion.resolve import client as resolve_client
from rushbase_worker.companion.selection import load_basket
from rushbase_worker.config import WorkerSettings
from rushbase_worker.hardware import detect_hardware

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
INSTRUCTIONS = (
    "Rushbase companion on the user's own PC: search the library, inspect assets, run local "
    "AI processing, build a selection and export it (FCPXML/EDL/CSV) or send it to DaVinci "
    "Resolve. No tool deletes or moves files."
)


def safe[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Report expected failures to the client as tool errors (not as server faults)."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except (
            service.CompanionError,
            resolve_client.ResolveError,
            UserApiError,
            ValueError,
            RuntimeError,
        ) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name)
    return cleaned or "export"


def build_server(
    settings: WorkerSettings, controller: ProcessController | None = None
) -> MCPServer:
    """Create the MCP server with all tools bound to ``settings``."""
    controller = controller or ProcessController()
    mcp = MCPServer("rushbase-companion", instructions=INSTRUCTIONS)

    @mcp.tool(annotations=READ_ONLY)
    @safe
    def search_media(
        query: str,
        filters: list[str] | None = None,
        level: str = "asset",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Hybrid search. `filters` are key=value strings (e.g. `media_type=video`, `tag=location:harbour`); `level` is asset or shot."""
        return service.search(settings, query, filters, level, limit, offset)

    @mcp.tool(annotations=READ_ONLY)
    @safe
    def get_asset(asset_id: str) -> dict[str, Any]:
        """Asset details: metadata, shots, tags, per-OS paths and the path on this machine."""
        return service.get_asset(settings, asset_id)

    @mcp.tool(annotations=READ_ONLY)
    @safe
    def get_keyframes(asset_id: str, shot_idx: int | None = None, limit: int = 6) -> list[Image]:
        """Keyframe images of an asset (or one shot), read from the local mount when available."""
        frames = service.keyframe_files(settings, asset_id, shot_idx, min(limit, 12))
        return [Image(data=service.read_keyframe(settings, f), format="jpeg") for f in frames]

    @mcp.tool(annotations=READ_ONLY)
    @safe
    def list_filters() -> dict[str, Any]:
        """Available filter dimensions with values (facets) for `search_media`."""
        return service.list_filters(settings)

    @mcp.tool(annotations=READ_ONLY)
    @safe
    def processing_status() -> dict[str, Any]:
        """Local processing run (running, counters, recent events) plus backlog and estimate."""
        return {
            "local_run": controller.status(),
            "overview": service.overview(settings, probe_mount=False),
        }

    @mcp.tool(annotations=WRITES)
    @safe
    def processing_start(
        stages: str | None = None, until: str | None = None, exit_when_empty: bool = True
    ) -> dict[str, Any]:
        """Start `rushbase process` on this PC (stages comma separated, until as HH:MM)."""
        return controller.start(stages, until, exit_when_empty)

    @mcp.tool(annotations=WRITES)
    @safe
    def processing_stop() -> dict[str, Any]:
        """Stop the local run gracefully: in-flight jobs are released back to the queue."""
        return controller.stop()

    @mcp.tool(annotations=READ_ONLY)
    @safe
    def hardware() -> dict[str, Any]:
        """Detected GPU(s), VRAM, backend and hardware profile."""
        return detect_hardware().model_dump(mode="json")

    @mcp.tool(annotations=READ_ONLY)
    @safe
    def selection_list() -> dict[str, Any]:
        """The local selection basket."""
        return load_basket(settings).model_dump(mode="json")

    @mcp.tool(annotations=WRITES)
    @safe
    def selection_add(asset_id: str, shots: list[int] | None = None) -> dict[str, Any]:
        """Add an asset (or only some shot indexes) to the basket."""
        return service.select_add(settings, asset_id, shots).model_dump(mode="json")

    @mcp.tool(annotations=WRITES)
    @safe
    def selection_remove(asset_id: str, shots: list[int] | None = None) -> dict[str, Any]:
        """Remove an asset (or some shots) from the basket."""
        return service.select_remove(settings, asset_id, shots).model_dump(mode="json")

    @mcp.tool(annotations=WRITES)
    @safe
    def selection_clear() -> dict[str, Any]:
        """Empty the basket."""
        return service.select_clear(settings).model_dump(mode="json")

    @mcp.tool(annotations=READ_ONLY)
    @safe
    def resolve_path(asset_id: str) -> dict[str, Any]:
        """Where an asset's files are on this machine (per-OS mapping and existence)."""
        return service.resolve_paths(settings, asset_id)

    @mcp.tool(annotations=READ_ONLY)
    @safe
    def resolve_status() -> dict[str, Any]:
        """DaVinci Resolve Studio: installed, version, reachable, open project."""
        return resolve_client.resolve_status(settings)

    @mcp.tool(annotations=WRITES)
    @safe
    def send_to_resolve(
        confirm: bool, bin_name: str = "Rushbase", markers: bool = False, metadata: bool = False
    ) -> dict[str, Any]:
        """Import the selection into the open Resolve project. `confirm` must be true (writes into the project)."""
        if not confirm:
            raise ValueError("confirm=true is required: this writes into the open Resolve project")
        return resolve_client.push_clips(
            settings, service.basket_clips(settings), lambda c: local_path(settings, c),
            bin_name=bin_name, markers=markers, metadata=metadata,
        )  # fmt: skip

    def export(kind: str, filename: str, columns: list[str] | None = None) -> dict[str, Any]:
        text, warnings = service.export_text(settings, kind, tuple(columns) if columns else None)
        target = settings.data_dir / "exports" / _safe_name(filename)
        service.write_export(target, text, kind)
        return {"file": str(target), "bytes": target.stat().st_size, "warnings": warnings}

    @mcp.tool(annotations=WRITES)
    @safe
    def export_fcpxml(filename: str = "rushbase.fcpxml") -> dict[str, Any]:
        """Write the selection as FCPXML 1.10 into the exports folder of the data directory."""
        return export("fcpxml", filename)

    @mcp.tool(annotations=WRITES)
    @safe
    def export_edl(filename: str = "rushbase.edl") -> dict[str, Any]:
        """Write the selection as a CMX 3600 EDL into the exports folder."""
        return export("edl", filename)

    @mcp.tool(annotations=WRITES)
    @safe
    def export_csv(
        filename: str = "rushbase.csv", columns: list[str] | None = None
    ) -> dict[str, Any]:
        """Write the Resolve metadata CSV of the selection into the exports folder."""
        return export("csv", filename, columns)

    return mcp


class BearerAuth:
    """ASGI middleware: every HTTP request needs ``Authorization: Bearer <token>``."""

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._token = token.encode()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            sent = headers.get(b"authorization", b"")
            expected = b"Bearer " + self._token
            if not hmac.compare_digest(sent, expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})  # fmt: skip
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self._app(scope, receive, send)


def serve(
    settings: WorkerSettings, *, http: str | None = None, token_file: Path | None = None
) -> None:
    """Run on stdio, or as streamable HTTP on ``HOST:PORT`` behind a bearer token."""
    controller = ProcessController()
    server = build_server(settings, controller)
    try:
        if http is None:
            server.run("stdio")
            return
        import uvicorn

        assert token_file is not None
        token = token_file.read_text(encoding="utf-8").strip()
        if len(token) < 16:
            raise ValueError("the token file must contain at least 16 characters")
        host, _, port = http.rpartition(":")
        uvicorn.run(BearerAuth(server.streamable_http_app(host=host or "127.0.0.1"), token),
                    host=host or "127.0.0.1", port=int(port), log_level="warning")  # fmt: skip
    finally:
        controller.stop(timeout=30.0)
