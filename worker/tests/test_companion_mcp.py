"""The local MCP server driven by the official SDK client (in memory and over real stdio)."""

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import ImageContent, TextContent

from companion_data import ASSET_ID, server_handler
from rushbase_worker.companion.mcp_server import build_server
from rushbase_worker.config import WorkerSettings

EXPECTED = {
    "search_media", "get_asset", "get_keyframes", "list_filters", "processing_status",
    "processing_start", "processing_stop", "hardware", "selection_list", "selection_add",
    "selection_remove", "selection_clear", "resolve_path", "resolve_status", "send_to_resolve",
    "export_fcpxml", "export_edl", "export_csv",
}  # fmt: skip


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WorkerSettings:
    root = tmp_path / "lib"
    frame = root / ".rushbase" / "derived" / ASSET_ID / "kf" / "0.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"\xff\xd8local\xff\xd9")
    monkeypatch.setenv("RUSHBASE_WORKER_CONFIG", str(tmp_path / "none.toml"))
    real = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: real(**{**kw, "transport": httpx.MockTransport(server_handler)}),
    )
    return WorkerSettings(server_url="http://srv", api_token="rbp_" + "t" * 30, library_root=root,
                          data_dir=tmp_path / "data")  # fmt: skip


async def call(client: Client, name: str, **args: Any) -> Any:
    result = await client.call_tool(name, args)
    assert not result.is_error, result
    return result


async def test_lists_every_tool_and_none_deletes(settings: WorkerSettings) -> None:
    async with Client(build_server(settings)) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == EXPECTED
    assert not [n for n in names if "delete" in n or n.startswith("move")]


async def test_search_asset_and_selection_flow(settings: WorkerSettings) -> None:
    async with Client(build_server(settings)) as client:
        found = (
            await call(
                client, "search_media", query="harbour", filters=["media_type=video"], level="shot"
            )
        ).structured_content
        assert found["items"][0]["asset"]["id"] == ASSET_ID
        asset = (await call(client, "get_asset", asset_id=ASSET_ID)).structured_content
        assert asset["local_path"].endswith("A001_C002.mov")
        added = (
            await call(client, "selection_add", asset_id=ASSET_ID, shots=[0])
        ).structured_content
        assert added["items"][0]["selected"] == [0]
        assert (await call(client, "selection_list")).structured_content["items"]
        paths = (await call(client, "resolve_path", asset_id=ASSET_ID)).structured_content
        assert paths["files"][0]["exists"] is False
        exported = (await call(client, "export_edl", filename="../escape.edl")).structured_content
        assert Path(exported["file"]).parent == settings.data_dir / "exports"
        assert "A001C002" in Path(exported["file"]).read_text()
        for tool in ("export_fcpxml", "export_csv"):
            assert Path((await call(client, tool)).structured_content["file"]).is_file()
        removed = (await call(client, "selection_remove", asset_id=ASSET_ID)).structured_content
        assert removed["items"] == []
        await call(client, "selection_clear")


async def test_keyframes_come_back_as_images(settings: WorkerSettings) -> None:
    async with Client(build_server(settings)) as client:
        result = await call(client, "get_keyframes", asset_id=ASSET_ID)
    images = [c for c in result.content if isinstance(c, ImageContent)]
    assert len(images) == 1 and images[0].mime_type == "image/jpeg"


async def test_remaining_tools(settings: WorkerSettings) -> None:
    async with Client(build_server(settings)) as client:
        assert (await call(client, "list_filters")).structured_content["facets"]
        assert "backend" in (await call(client, "hardware")).structured_content
        status = (await call(client, "processing_status")).structured_content
        assert status["local_run"]["running"] is False and "overview" in status
        assert (await call(client, "processing_stop")).structured_content["stopped"] is False
        assert "installed" in (await call(client, "resolve_status")).structured_content


async def test_send_to_resolve_needs_confirmation(settings: WorkerSettings) -> None:
    async with Client(build_server(settings)) as client:
        result = await client.call_tool("send_to_resolve", {"confirm": False})
    assert result.is_error
    assert any(isinstance(c, TextContent) and "confirm" in c.text for c in result.content)


async def test_errors_are_tool_errors(settings: WorkerSettings) -> None:
    async with Client(build_server(settings)) as client:
        result = await client.call_tool("get_asset", {"asset_id": "nope"})
    assert result.is_error


async def test_real_stdio_transport(settings: WorkerSettings, tmp_path: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from rushbase_worker.cli import app; app()", "mcp", "serve", "--stdio"],
        env={"RUSHBASE_DATA_DIR": str(tmp_path / "d"), "RUSHBASE_WORKER_CONFIG": str(tmp_path / "n.toml"),
             "PATH": "/usr/bin:/bin"},
    )  # fmt: skip
    async with Client(params) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert names == EXPECTED
        assert (await client.call_tool("selection_list", {})).structured_content["items"] == []
