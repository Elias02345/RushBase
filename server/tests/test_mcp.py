import base64
import io
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx2
import pytest
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import CallToolResult, ImageContent, TextContent
from PIL import Image
from sqlalchemy import select

from ai_support import (
    audio_output,
    classify_output,
    description,
    embed_output,
    estimate,
    fake_job,
    seed_shots_asset,
    seed_taxonomy,
    tag,
    vision_output,
)
from rushbase_core import mcp_tools as spec
from rushbase_server.mcp.output import ToolFailure, decode_cursor, encode_cursor, fit
from rushbase_server.models import Asset, AssetTag, McpAudit, Shot, User
from rushbase_server.services import ai_results
from support import Server
from test_oauth import MCP, PUBLIC, grant

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


@dataclass
class Fixture:
    server: Server
    asset: Asset
    other: Asset
    tax: dict[str, UUID]
    root: Path


def _jpeg(path: Path, size: tuple[int, int] = (1200, 800)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (30, 90, 160)).save(path, "JPEG")


async def _seed(server: Server, root: Path) -> Fixture:
    async with server.runtime.session_factory() as session:
        tax = await seed_taxonomy(session)
        asset = await seed_shots_asset(session, shots=2)
        other = await seed_shots_asset(session, shots=1)
        for item, text in ((asset, "Hafen"), (other, "Wald")):
            await ai_results.apply_output(
                session,
                fake_job(item.id, "vision"),
                vision_output(
                    [description(0, text, shot_size=estimate("wide", 0.2))], summary=text
                ),
            )
        await ai_results.apply_output(
            session, fake_job(asset.id, "classify"), classify_output([tag("harbour", 0)])
        )
        await ai_results.apply_output(
            session, fake_job(asset.id, "audio"), audio_output(["erster Satz", "zweiter Satz"])
        )
        await ai_results.apply_output(session, fake_job(asset.id, "embed"), embed_output([0, 1]))
        await ai_results.apply_output(session, fake_job(other.id, "embed"), embed_output([0]))
        rel = f".rushbase/derived/{asset.id}"
        await session.refresh(asset)
        asset.derived = {"files": [{"kind": "poster", "rel_path": f"{rel}/poster.jpg"}]}
        for shot in (
            await session.execute(select(Shot).where(Shot.asset_id == asset.id))
        ).scalars():
            shot.keyframes = [
                {"t_ms": 500, "rel_path": f"{rel}/kf{shot.idx}.jpg", "position": "middle"}
            ]
        await session.commit()
        _jpeg(root / rel / "poster.jpg")
        _jpeg(root / rel / "kf0.jpg", (1600, 900))
        _jpeg(root / rel / "kf1.jpg", (900, 1600))
        return Fixture(server, asset, other, tax, root)


@asynccontextmanager
async def mcp_client(server: Server, scope: str) -> AsyncIterator[Client]:
    _, tokens = await grant(server, scope)
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=server.app),
        base_url=PUBLIC,
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    async with http, Client(streamable_http_client(MCP, http_client=http)) as client:
        yield client


ALL = " ".join(spec.SCOPES)


async def call(client: Client, tool: str, **args: Any) -> tuple[dict[str, Any], CallToolResult]:
    result = await client.call_tool(tool, args)
    assert not result.is_error, result.content
    first = result.content[0]
    assert isinstance(first, TextContent)
    assert len(first.text) <= spec.MAX_RESULT_CHARS
    return json.loads(first.text), result


def images(result: CallToolResult) -> list[ImageContent]:
    return [c for c in result.content if isinstance(c, ImageContent)]


def dimensions(content: ImageContent) -> tuple[int, int]:
    return Image.open(io.BytesIO(base64.b64decode(content.data))).size


def scenario(
    body: Callable[[Fixture], Awaitable[None]],
) -> Callable[[ServerFactory, str, Path], Awaitable[None]]:
    """Run a test body against a server with seeded data (lifespan stays in one task)."""

    async def run(server_factory: ServerFactory, database_url: str, library_root: Path) -> None:
        async with server_factory(
            database_url, public_url=PUBLIC, library_root=library_root
        ) as server:
            await server.setup_admin()
            await body(await _seed(server, library_root))

    run.__name__ = body.__name__
    return run


@scenario
async def test_unauthenticated_gets_challenge(env: Fixture) -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=env.server.app), base_url=PUBLIC
    ) as http:
        response = await http.post(MCP, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert response.status_code == 401
        challenge = response.headers["www-authenticate"]
        assert f'resource_metadata="{PUBLIC}/.well-known/oauth-protected-resource/mcp"' in challenge
        assert 'scope="read"' in challenge
        bad = await http.post(
            MCP,
            headers={"Authorization": "Bearer nope"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        assert bad.status_code == 401
        wrong_host = await http.post(
            "http://evil.example/mcp",
            headers={"Authorization": "Bearer nope"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        assert wrong_host.status_code in {401, 421}


@scenario
async def test_tool_list_contract(env: Fixture) -> None:
    async with mcp_client(env.server, ALL) as client:
        tools = (await client.list_tools()).tools
        assert {t.name for t in tools} == {t.name for t in spec.TOOLS}
        for tool in tools:
            assert len(json.dumps(tool.input_schema)) < spec.MAX_SCHEMA_BYTES, tool.name
            assert tool.description
        resources = {str(r.uri) for r in (await client.list_resources()).resources}
        assert resources == {
            "rushbase://library/overview",
            "rushbase://taxonomy",
            "rushbase://rules",
        }
        overview = await client.read_resource("rushbase://library/overview")
        assert "assets_by_status" in overview.contents[0].text  # type: ignore[union-attr]


def test_no_destructive_tools() -> None:
    for tool in spec.TOOLS:
        assert not any(word in tool.name for word in spec.FORBIDDEN_TOOL_WORDS), tool.name
    assert len({t.name for t in spec.TOOLS}) == len(spec.TOOLS)
    assert all(len(spec.BY_NAME[t.name].description) > 20 for t in spec.TOOLS)
    assert max(spec.schema_size(t) for t in spec.TOOLS) < spec.MAX_SCHEMA_BYTES


@scenario
async def test_read_tools(env: Fixture) -> None:
    async with mcp_client(env.server, ALL) as client:
        found, result = await call(
            client, "search_media", q="Hafen", include_thumbnails=True, limit=5
        )
        assert found["items"][0]["id"] == str(env.asset.id)
        pictures = images(result)
        assert pictures
        assert max(dimensions(pictures[0])) <= 256
        shots, _ = await call(client, "search_media", q="Hafen", level="shot")
        assert shots["items"][0]["shot"]["idx"] == 0
        tagged, _ = await call(
            client, "search_media", q="Hafen", filters={"tags": ["location:harbour"]}
        )
        assert tagged["total"] >= 1

        detail, _ = await call(client, "get_asset", asset_id=str(env.asset.id), transcript_limit=1)
        assert detail["transcript"]["total_segments"] == 2
        assert detail["transcript"]["next_offset"] == 1
        page2, _ = await call(
            client, "get_asset", asset_id=str(env.asset.id), transcript_offset=1, transcript_limit=1
        )
        assert page2["transcript"]["segments"][0]["text"] == "zweiter Satz"

        _, result = await call(client, "get_keyframes", asset_id=str(env.asset.id), limit=6)
        sizes = [dimensions(i) for i in images(result)]
        assert sizes
        assert all(max(s) <= spec.MAX_IMAGE_EDGE for s in sizes)
        assert len(images(result)) <= spec.MAX_IMAGES

        similar, _ = await call(client, "find_similar", asset_id=str(env.asset.id))
        assert any(i["id"] == str(env.other.id) for i in similar["items"])

        facets, _ = await call(client, "list_facets")
        assert any(f["key"] == "media_type" for f in facets["facets"])
        counts, _ = await call(client, "facet_counts", facet="media_type")
        assert counts["values"][0]["count"] >= 2
        stats, _ = await call(client, "library_stats")
        assert stats["assets_by_status"]["filed"] == 2
        matrix, _ = await call(
            client,
            "coverage_report",
            rows="location",
            columns="media_type",
            include_last_capture=True,
        )
        assert {"row": "harbour", "col": "video", "count": 1, "last_capture": None} in matrix[
            "cells"
        ]
        bad = await client.call_tool(
            "coverage_report", {"rows": "nonsense", "columns": "media_type"}
        )
        assert bad.is_error
        gaps, _ = await call(
            client,
            "gap_analysis",
            items=[
                {"description": "Hafen", "count": 1},
                {"description": "Raumschiff", "count": 2},
            ],
        )
        assert [i["state"] for i in gaps["items"]] == ["covered", "missing"]
        status, _ = await call(client, "processing_status")
        assert "workers_online" in status


@scenario
async def test_pagination_cursor_and_size_cap(env: Fixture) -> None:
    async with env.server.runtime.session_factory() as session:
        for _ in range(4):
            extra = await seed_shots_asset(session, shots=1)
            await ai_results.apply_output(
                session,
                fake_job(extra.id, "vision"),
                vision_output([description(0, "Hafen")], summary="Hafen"),
            )
        await session.commit()
    async with mcp_client(env.server, ALL) as client:
        first, _ = await call(client, "search_media", q="Hafen", limit=2)
        assert len(first["items"]) == 2
        second, _ = await call(
            client, "search_media", q="Hafen", limit=2, cursor=first["next_cursor"]
        )
        assert {i["id"] for i in first["items"]}.isdisjoint(i["id"] for i in second["items"])
        broken = await client.call_tool("search_media", {"q": "Hafen", "cursor": "%%%"})
        assert broken.is_error


def test_fit_trims_lists_and_sets_cursor() -> None:
    items = [{"text": "x" * 500} for _ in range(200)]
    text = fit({"items": items, "total": 200}, "items", 10)
    data = json.loads(text)
    assert len(text) <= spec.MAX_RESULT_CHARS
    assert 0 < len(data["items"]) < 200
    assert decode_cursor(data["next_cursor"]) == 10 + len(data["items"])
    assert decode_cursor(encode_cursor(7)) == 7
    with pytest.raises(ToolFailure):
        fit({"blob": "y" * 40_000})
    assert (
        "history"
        in json.loads(fit({"a": 1, "history": ["z" * 40_000]}, droppable=("history",)))["omitted"]
    )


@scenario
async def test_scope_enforcement(env: Fixture) -> None:
    async with mcp_client(env.server, "read") as client:
        denied = await client.call_tool(
            "annotate_asset", {"asset_id": str(env.asset.id), "rating": 4}
        )
        assert denied.is_error
        assert "scope" in str(denied.content[0])
        needs_denied = await client.call_tool("needs_list", {})
        assert needs_denied.is_error
        allowed, _ = await call(client, "taxonomy_list")
        assert allowed["fields"]


@scenario
async def test_annotate_precedence_and_audit(env: Fixture) -> None:
    async with env.server.runtime.session_factory() as session:
        session.add(
            AssetTag(
                asset_id=env.asset.id,
                field_id=env.tax["field"],
                value_id=env.tax["street"],
                source="human",
                confidence=1.0,
            )
        )
        await session.commit()
    async with mcp_client(env.server, ALL) as client:
        done, _ = await call(
            client,
            "annotate_asset",
            asset_id=str(env.asset.id),
            add_tags=["location:harbour"],
            rating=4,
            note="gute Totale",
        )
        assert done["applied"]["columns"]["rating"]["to"] == 4
        removed, _ = await call(
            client, "annotate_asset", asset_id=str(env.asset.id), remove_tags=["location:street"]
        )
        assert "tags_removed" not in removed["applied"]  # human tags are out of reach
        unknown = await client.call_tool(
            "annotate_asset", {"asset_id": str(env.asset.id), "add_tags": ["location:moon"]}
        )
        assert unknown.is_error
    async with env.server.runtime.session_factory() as session:
        sources = {
            (row.source, row.value_id)
            for row in (
                await session.execute(select(AssetTag).where(AssetTag.asset_id == env.asset.id))
            ).scalars()
        }
        assert ("human", env.tax["street"]) in sources
        asset = await session.get_one(Asset, env.asset.id)
        assert asset.ai_details["notes"][0]["text"] == "gute Totale"
        rows = (await session.execute(select(McpAudit))).scalars().all()
        assert {"annotate_asset"} <= {r.tool for r in rows}
        assert all(len(r.args_hash) == 64 for r in rows)
        assert any(not r.ok for r in rows)


@scenario
async def test_needs_flow_and_auto_matching(env: Fixture) -> None:
    async with mcp_client(env.server, ALL) as client:
        created, _ = await call(
            client,
            "needs_create",
            title="Imagefilm",
            purpose="Intro",
            items=[{"description": "Raumschiff"}, {"description": "Hafen", "count": 1}],
        )
        item_id = next(i["id"] for i in created["items"] if i["description"] == "Hafen")
        space_id = next(i["id"] for i in created["items"] if i["description"] == "Raumschiff")
        # auto-matching is passive: it attaches candidates when an asset is (re)classified
        await env.server.runtime.match_needs(env.asset.id)
        await env.server.runtime.match_needs(env.other.id)
        listing, _ = await call(client, "needs_list")
        assert listing["lists"][0]["candidates"] == 1
        got, result = await call(
            client, "needs_get", need_id=created["id"], include_thumbnails=True
        )
        hafen = next(i for i in got["items"] if i["id"] == item_id)
        assert hafen["candidates"][0]["source"] == "auto"
        assert hafen["candidates"][0]["asset_id"] == str(env.asset.id)
        assert images(result)
        linked, _ = await call(
            client,
            "needs_link_candidate",
            item_id=space_id,
            asset_id=str(env.other.id),
            status="confirmed",
        )
        assert linked["item_status"] == "fulfilled"
        await call(
            client,
            "needs_update",
            need_id=created["id"],
            title="Imagefilm 2",
            add_items=[{"description": "Wald"}],
        )
        closed, _ = await call(client, "needs_close", need_id=created["id"])
        assert closed["status"] == "closed"
    # closed lists get no new matches, and the REST API shows the same list
    await env.server.runtime.match_needs(env.asset.id)
    page = (await env.server.client.get("/api/v1/needs")).json()
    assert page["items"][0]["status"] == "closed"
    assert page["items"][0]["candidates"] == 2  # one automatic, one linked by Claude


@scenario
async def test_taxonomy_rules_and_reprocess(env: Fixture) -> None:
    async with mcp_client(env.server, ALL) as client:
        proposed, _ = await call(
            client,
            "taxonomy_propose_value",
            field="location",
            value="airport",
            synonyms=["flughafen"],
        )
        assert proposed["status"] == "proposed"
        approved, _ = await call(client, "taxonomy_approve", value_id=proposed["id"])
        assert approved["status"] == "approved"
        syn, _ = await call(
            client, "taxonomy_add_synonym", value_id=proposed["id"], synonym="terminal"
        )
        assert "terminal" in syn["synonyms"]
        merged, _ = await call(
            client, "taxonomy_merge", value_id=proposed["id"], target_id=str(env.tax["street"])
        )
        assert merged["remaining"] == str(env.tax["street"])

        current, _ = await call(client, "rules_get")
        assert current["active"] is not None or current["recent_versions"] == []
        proposal, _ = await call(
            client,
            "rules_propose",
            template="Content/{media_type}/{capture_date:%Y}/{batch_label|Unsorted}/",
            note="by year",
        )
        assert proposal["dry_run"]["assets"] >= 0
        activated, _ = await call(client, "rules_activate", rule_id=proposal["rule"]["id"])
        assert activated["active"] is True
        invalid = await client.call_tool("rules_propose", {"template": "{nonsense("})
        assert invalid.is_error

        queued, _ = await call(client, "reprocess", stage="classify", asset_ids=[str(env.asset.id)])
        assert queued["queued"] == 1
        again, _ = await call(client, "reprocess", stage="classify", asset_ids=[str(env.asset.id)])
        assert again["queued"] == 0  # an open job exists already
        by_filter, _ = await call(
            client, "reprocess", stage="embed", filters={"media_type": "video"}
        )
        assert by_filter["queued"] == 2
        both = await client.call_tool("reprocess", {"stage": "embed"})
        assert both.is_error
    assert not list((env.root / "Content").rglob("*"))  # MCP never creates or moves content


@scenario
async def test_role_limits_apply_to_the_connected_account(env: Fixture) -> None:
    async with env.server.runtime.session_factory() as session:
        admin = (await session.execute(select(User).where(User.username == "admin"))).scalar_one()
        admin.role = "editor"
        await session.commit()
    async with mcp_client(env.server, ALL) as client:
        denied = await client.call_tool(
            "taxonomy_merge",
            {"value_id": str(env.tax["street"]), "target_id": str(env.tax["harbour"])},
        )
        assert denied.is_error
        assert "admin" in str(denied.content[0])
        approved, _ = await call(client, "library_stats")
        assert approved
