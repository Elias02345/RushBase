from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from rushbase_core import uuid7
from rushbase_server.models import Asset, AssetFile, Batch, Shot
from support import ALEMBIC_INI, Server

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


async def add_asset(
    server: Server, path: str, *, batch_label: str = "Misc", **columns: Any
) -> Asset:
    """Insert an asset with one primary file (triggers maintain the search vector)."""
    async with server.runtime.session_factory() as session:
        batch = Batch(id=uuid7(), input_path=f"Input/{uuid7()}", label=batch_label)
        session.add(batch)
        await session.flush()
        asset = Asset(id=uuid7(), batch_id=batch.id, media_type="video", **columns)
        session.add(asset)
        await session.flush()
        file = AssetFile(
            id=uuid7(),
            asset_id=asset.id,
            role="primary",
            rel_path=path,
            original_rel_path=path,
            size=1,
        )
        session.add(file)
        await session.flush()
        asset.primary_file_id = file.id
        await session.commit()
        return asset


async def search(server: Server, query: str) -> list[str]:
    response = await server.client.get("/api/v1/assets", params={"q": query})
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


async def test_accents_umlauts_and_prefix(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        strasse = await add_asset(server, "Content/Strasse_01.mov", title="Straße am Hafen")
        cafe = await add_asset(server, "Content/cafe.mov", title="Café Szene")
        wedding = await add_asset(server, "Content/clip.mov", title="Wedding Day")
        for query in ("strasse", "Straße", "STRASSE"):
            assert await search(server, query) == [str(strasse.id)]
        assert await search(server, "cafe") == [str(cafe.id)]
        assert await search(server, "Café") == [str(cafe.id)]
        assert await search(server, "wedd") == [str(wedding.id)]
        assert await search(server, "wedding day") == [str(wedding.id)]
        assert await search(server, "day wedd") == [str(wedding.id)]
        assert await search(server, "weddings") == []


async def test_filename_batch_summary_and_path_are_searchable(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        asset = await add_asset(
            server,
            "Content/2026/Harbour/IMG_1234.mov",
            batch_label="Hochzeit Anna",
            summary_short="Brautpaar am Strand",
            camera_model="ILME-FX3",
        )
        for query in ("IMG_1234", "1234", "hochzeit", "brautpaar", "strand", "fx3", "harbour"):
            assert await search(server, query) == [str(asset.id)], query
        # a later batch rename and a moved file refresh the vector through the triggers
        async with server.runtime.session_factory() as session:
            batch = await session.get(Batch, asset.batch_id)
            assert batch is not None
            batch.label = "Taufe"
            file = await session.get(AssetFile, asset.primary_file_id)
            assert file is not None
            file.rel_path = "Content/2026/Quay/DSC_9.mov"
            await session.commit()
        assert await search(server, "taufe") == [str(asset.id)]
        assert await search(server, "hochzeit") == []
        assert await search(server, "DSC_9") == [str(asset.id)]
        assert await search(server, "harbour") == []


async def test_title_outranks_path_match_and_cursor_pages(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        path_only = await add_asset(server, "Content/sunset/a.mov")
        summary = await add_asset(server, "Content/b.mov", summary_short="sunset over water")
        title = await add_asset(server, "Content/c.mov", title="Sunset")
        expected = [str(title.id), str(summary.id), str(path_only.id)]
        assert await search(server, "sunset") == expected
        params: dict[str, Any] = {"q": "sunset", "limit": 1}
        seen: list[str] = []
        while True:
            page = (await server.client.get("/api/v1/assets", params=params)).json()
            seen += [item["id"] for item in page["items"]]
            if not page["next_cursor"]:
                break
            params["cursor"] = page["next_cursor"]
        assert seen == expected
        # an explicit sort wins over relevance
        by_name = (
            await server.client.get("/api/v1/assets", params={"q": "sunset", "sort": "filename"})
        ).json()
        assert [i["id"] for i in by_name["items"]] == [
            str(path_only.id),
            str(summary.id),
            str(title.id),
        ]


async def test_weird_input_never_fails(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        asset = await add_asset(server, "Content/foo.mov", title="foo bar")
        weird = ("a & | ( !", "foo & !", "'", "\\", "foo:*", "(((", "<->", "foo | bar", "%", "ß:*")
        for query in weird:
            response = await server.client.get("/api/v1/assets", params={"q": query})
            assert response.status_code == 200, query
            facets = await server.client.get("/api/v1/facets", params={"q": query})
            assert facets.status_code == 200, query
        assert await search(server, "foo & bar") == [str(asset.id)]
        assert await search(server, "foo | zzz") == []


async def test_short_query_falls_back_to_path_substring(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        hit = await add_asset(server, "Content/xq_clip.mov")
        await add_asset(server, "Content/other.mov")
        assert await search(server, "xq") == [str(hit.id)]
        assert await search(server, "q_") == [str(hit.id)]
        assert await search(server, "%") == []


async def test_facets_respect_filters_and_query(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        await add_asset(server, "Content/a.mov", title="Hafen", camera_model="FX3", lens_model="L1")
        await add_asset(server, "Content/b.mov", title="Hafen", camera_model="A7", lens_model="L1")
        await add_asset(server, "Content/c.mov", title="Wald", camera_model="A7", lens_model="L2")

        def values(body: dict[str, Any], key: str) -> dict[str, int]:
            facet = next(f for f in body["facets"] if f["key"] == key)
            return {v["value"]: v["count"] for v in facet["values"]}

        get = server.client.get
        plain = (await get("/api/v1/facets")).json()
        assert values(plain, "camera_model") == {"A7": 2, "FX3": 1}
        by_query = (await get("/api/v1/facets", params={"q": "hafen"})).json()
        assert values(by_query, "camera_model") == {"A7": 1, "FX3": 1}
        assert values(by_query, "lens_model") == {"L1": 2}
        # other filters narrow every facet; a facet ignores its own filter
        narrowed = (
            await get("/api/v1/facets", params={"q": "hafen", "camera_model": "FX3"})
        ).json()
        assert values(narrowed, "lens_model") == {"L1": 1}
        assert values(narrowed, "camera_model") == {"A7": 1, "FX3": 1}


def test_search_migration_backfills_and_roundtrips(
    monkeypatch: pytest.MonkeyPatch, fresh_database_url: str
) -> None:
    monkeypatch.setenv("RUSHBASE_SECRET_KEY", "m" * 40)
    monkeypatch.setenv("RUSHBASE_DATABASE_URL", fresh_database_url)
    config = Config(str(ALEMBIC_INI))
    command.upgrade(config, "0003")
    engine = create_engine(fresh_database_url)
    asset_id, file_id, batch_id = uuid7(), uuid7(), uuid7()
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO batch (id, input_path, label) VALUES (:b, 'Input/x', 'Reise')"),
            {"b": batch_id},
        )
        connection.execute(
            text(
                "INSERT INTO asset (id, batch_id, media_type, title, primary_file_id) "
                "VALUES (:a, :b, 'video', 'Straße', :f)"
            ),
            {"a": asset_id, "b": batch_id, "f": file_id},
        )
        connection.execute(
            text(
                "INSERT INTO asset_file (id, asset_id, role, rel_path, original_rel_path, size) "
                "VALUES (:f, :a, 'primary', 'Content/Clip_7.mov', 'Input/x/Clip_7.mov', 1)"
            ),
            {"a": asset_id, "f": file_id},
        )
    query = (
        "SELECT count(*) FROM asset WHERE search_tsv @@ "
        "to_tsquery('rushbase_simple', 'strasse & clip & reise')"
    )
    command.upgrade(config, "head")
    command.upgrade(config, "head")  # idempotent
    with engine.connect() as connection:
        assert connection.execute(text(query)).scalar_one() == 1
    command.downgrade(config, "0003")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT search_tsv FROM asset")).scalar_one() is None
        names = set(connection.execute(text("SELECT cfgname FROM pg_ts_config")).scalars())
    assert "rushbase_simple" not in names
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text(query)).scalar_one() == 1
    engine.dispose()


async def test_shot_vector_is_maintained(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        asset = await add_asset(server, "Content/s.mov")
        async with server.runtime.session_factory() as session:
            session.add(
                Shot(
                    id=uuid7(),
                    asset_id=asset.id,
                    idx=0,
                    start_ms=0,
                    end_ms=10,
                    description_short="Möwe über dem Hafen",
                )
            )
            await session.commit()
            count = await session.scalar(
                text(
                    "SELECT count(*) FROM shot "
                    "WHERE search_tsv @@ to_tsquery('rushbase_simple', 'mowe & hafen')"
                )
            )
        assert count == 1
