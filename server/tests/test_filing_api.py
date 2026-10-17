import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, select

from pipeline import enroll, finalize_all, put_file, run_workers, scan
from rushbase_server.models import Asset, AssetFile, AssetMetadata, Batch, Job, Shot
from rushbase_server.services import rebuild
from support import Server

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


async def filed_server_state(server: Server, admin: dict[str, str], root: Path) -> None:
    put_file(root, "Input/Harbour/clip.mov", b"video-bytes")
    put_file(root, "Input/Harbour/IMG_1.CR3", b"raw-bytes")
    await scan(server)
    worker = await enroll(server, admin)
    await run_workers(server, worker, root)
    await finalize_all(server)


async def test_asset_list_detail_facets_stats_and_batches(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        await server.client.put(
            "/api/v1/admin/library-settings",
            json={"path_roots": {"windows": "Z:\\Lib", "linux": "/mnt/lib"}, "central_meta": True},
            headers=admin,
        )
        await filed_server_state(server, admin, library_root)
        client = server.client

        page = (await client.get("/api/v1/assets?limit=1&sort=filename")).json()
        assert page["total"] == 2
        assert page["items"][0]["filename"] == "clip.mov"
        assert page["next_cursor"]
        second = (
            await client.get(f"/api/v1/assets?limit=1&sort=filename&cursor={page['next_cursor']}")
        ).json()
        assert second["items"][0]["filename"] == "IMG_1.CR3"
        assert second["next_cursor"] is None
        assert second["total"] is None
        assert (await client.get("/api/v1/assets?cursor=garbage")).status_code == 422
        assert (await client.get("/api/v1/assets?sort=bogus")).status_code == 422

        by_name = (await client.get("/api/v1/assets?q=CLIP")).json()
        assert [i["filename"] for i in by_name["items"]] == ["clip.mov"]
        assert (await client.get("/api/v1/assets?q=%25")).json()["items"] == []
        filtered = (
            await client.get("/api/v1/assets?camera_model=ILME-FX3&media_type=video")
        ).json()
        assert filtered["total"] == 1
        assert (await client.get("/api/v1/assets?date_from=2026-03-10")).json()["items"] == []

        item = by_name["items"][0]
        assert item["poster_url"].startswith(f"/api/v1/derived/{item['id']}/poster.jpg")
        assert item["batch_label"] == "Harbour"
        detail = (await client.get(f"/api/v1/assets/{item['id']}")).json()
        assert detail["status"] == "filed"
        assert detail["files"][0]["paths"]["windows"].startswith("Z:\\Lib\\Content\\")
        assert detail["files"][0]["paths"]["linux"].startswith("/mnt/lib/Content/")
        assert detail["metadata"]["normalized"]["camera"]["model"] == "ILME-FX3"
        assert detail["shots"][0]["keyframes"][0]["url"].endswith("kf0.jpg")
        assert {h["action"] for h in detail["history"]} == {"asset.filed"}
        assert (library_root / ".rushbase/meta" / f"{item['id']}.json").is_file()
        other = item["id"][:-1] + ("1" if item["id"][-1] == "0" else "0")
        assert (await client.get(f"/api/v1/assets/{other}")).status_code == 404

        facets = {f["key"]: f for f in (await client.get("/api/v1/facets")).json()["facets"]}
        assert facets["camera_model"]["values"] == [{"value": "ILME-FX3", "count": 2}]
        assert facets["duration_ms"]["kind"] == "range"
        assert facets["capture_at"]["kind"] == "date"

        stats = (await client.get("/api/v1/stats")).json()
        assert stats["storage"]["total_bytes"] == len(b"video-bytes") + len(b"raw-bytes")
        assert {e["key"] for e in stats["storage"]["by_media_type"]} == {"video", "photo"}
        assert stats["storage"]["by_year"][0]["year"] == 2026
        assert stats["duplicates"] == {"count": 0, "wasted_bytes": 0}
        assert "finalize" in stats["backlog"]

        batches = (await client.get("/api/v1/batches")).json()
        assert batches[0]["by_status"] == {"filed": 2}
        assert batches[0]["status"] == "done"


async def test_filing_rules_dry_run_activation_and_validation(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        await filed_server_state(server, admin, library_root)
        client = server.client
        rules = (await client.get("/api/v1/filing-rules")).json()
        assert [(r["version"], r["active"]) for r in rules] == [(1, True)]
        bad = await client.post(
            "/api/v1/filing-rules", json={"template": "Content/{nope}"}, headers=admin
        )
        assert bad.status_code == 422
        template = "Content/{field.brand|General}/{camera_model}/{capture_date:%Y-%m}"
        dry = (
            await client.post(
                "/api/v1/filing-rules/dry-run", json={"template": template}, headers=admin
            )
        ).json()
        assert {i["path"] for i in dry["items"]} == {"Content/General/ILME-FX3/2026-03"}
        assert dry["directories"] == 1
        assert dry["conflicts"] == []
        long = await client.post(
            "/api/v1/filing-rules/dry-run",
            json={"template": "Content/{batch_label}/{batch_label}/{batch_label}"},
            headers=admin,
        )
        assert long.status_code == 200
        created = (
            await client.post(
                "/api/v1/filing-rules",
                json={"template": template, "note": "by camera"},
                headers=admin,
            )
        ).json()
        assert created["version"] == 2
        assert created["active"] is False
        activated = await client.post(
            f"/api/v1/filing-rules/{created['id']}/activate", headers=admin
        )
        assert activated.json()["active"] is True
        rules = (await client.get("/api/v1/filing-rules")).json()
        assert [(r["version"], r["active"]) for r in rules] == [(2, True), (1, False)]

        # already filed assets stay where they are; new ones follow the new rule
        put_file(library_root, "Input/Second/other.mov", b"second-clip")
        await scan(server)
        worker = await enroll(server, admin)
        await run_workers(server, worker, library_root)
        await finalize_all(server)
        async with server.runtime.session_factory() as session:
            dirs = {a.content_rel_dir for a in (await session.execute(select(Asset))).scalars()}
        assert dirs == {
            "Content/video/2026/2026-03-09_Harbour",
            "Content/photo/2026/2026-03-09_Harbour",
            "Content/General/ILME-FX3/2026-03",
        }


async def test_derived_files_are_served_safely(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        await filed_server_state(server, admin, library_root)
        client = server.client
        asset = (await client.get("/api/v1/assets?q=clip")).json()["items"][0]
        url = asset["poster_url"]
        ok = await client.get(url)
        assert ok.status_code == 200
        assert ok.content == b"jpeg"
        assert "max-age" in ok.headers["cache-control"]
        ranged = await client.get(url, headers={"Range": "bytes=1-2"})
        assert ranged.status_code == 206
        assert ranged.content == b"pe"

        asset_id = asset["id"]
        secret = library_root / "secret.txt"
        secret.write_text("secret")
        other = library_root / ".rushbase/derived/other/x.jpg"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_bytes(b"x")
        link = library_root / f".rushbase/derived/{asset_id}/link.jpg"
        link.symlink_to(secret)
        for attack in (
            f"/api/v1/derived/{asset_id}/../../../secret.txt",
            f"/api/v1/derived/{asset_id}/%2e%2e/%2e%2e/%2e%2e/secret.txt",
            f"/api/v1/derived/{asset_id}/..%2f..%2f..%2fsecret.txt",
            f"/api/v1/derived/{asset_id}//etc/passwd",
            f"/api/v1/derived/{asset_id}/link.jpg",
            f"/api/v1/derived/{asset_id}/missing.jpg",
            f"/api/v1/derived/{asset_id}/%5c..%5csecret.txt",
        ):
            response = await client.get(attack)
            assert response.status_code in (404, 400), attack
            assert b"secret" not in response.content
        # the original is never reachable, whatever the path
        original = (await client.get(f"/api/v1/assets/{asset_id}")).json()["files"][0]["rel_path"]
        assert (await client.get(f"/api/v1/derived/{asset_id}/{original}")).status_code == 404

        client.cookies.clear()
        assert (await client.get(url)).status_code == 401


async def test_duplicate_of_a_filed_asset_stays_in_input(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        await filed_server_state(server, admin, library_root)
        put_file(library_root, "Input/Again/copy-of-clip.mov", b"video-bytes")
        await scan(server)
        worker = await enroll(server, admin)
        await run_workers(server, worker, library_root)
        await finalize_all(server)
        client = server.client
        dup = (await client.get("/api/v1/assets?status=duplicate")).json()["items"]
        assert [d["filename"] for d in dup] == ["copy-of-clip.mov"]
        detail = (await client.get(f"/api/v1/assets/{dup[0]['id']}")).json()
        assert detail["duplicate_of"] is not None
        assert detail["content_rel_dir"] is None
        assert (library_root / "Input/Again/copy-of-clip.mov").read_bytes() == b"video-bytes"
        stats = (await client.get("/api/v1/stats")).json()
        assert stats["duplicates"] == {"count": 1, "wasted_bytes": len(b"video-bytes")}
        async with server.runtime.session_factory() as session:
            open_jobs = (
                (
                    await session.execute(
                        select(Job).where(
                            Job.asset_id == detail["id"], Job.state.in_(("queued", "blocked"))
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert open_jobs == []


async def test_invalid_output_fails_the_job_and_quarantine_retry_round_trips(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        put_file(library_root, "Input/Bad/broken.mov", b"broken")
        await scan(server)
        worker = await enroll(server, admin)
        client = server.client
        jobs = (
            await client.post("/api/v1/worker/claim", json={"max_jobs": 5}, headers=worker)
        ).json()["jobs"]
        probe = next(j for j in jobs if j["stage"] == "probe")
        response = await client.post(
            f"/api/v1/worker/jobs/{probe['job_id']}/result",
            json={"ok": True, "output": {"video": {"fps": "fast"}, "bogus": 1}},
            headers=worker,
        )
        assert response.json()["job_state"] == "failed"
        await finalize_all(server)

        quarantined = (await client.get("/api/v1/quarantine")).json()
        assert len(quarantined) == 1
        item = quarantined[0]
        assert item["stage"] == "probe"
        assert "invalid probe output" in item["reason"]
        assert item["moved"] is True
        qdir = library_root / ".rushbase/quarantine" / item["asset_id"]
        assert (qdir / "Input/Bad/broken.mov").read_bytes() == b"broken"
        assert json.loads((qdir / "error.json").read_text())["stage"] == "probe"
        assert not (library_root / "Input/Bad/broken.mov").exists()

        # retry is refused while the original location is occupied, nothing moves
        put_file(library_root, "Input/Bad/broken.mov", b"someone else")
        refused = await client.post(f"/api/v1/quarantine/{item['asset_id']}/retry", headers=admin)
        assert refused.status_code == 409
        assert (qdir / "Input/Bad/broken.mov").exists()
        (library_root / "Input/Bad/broken.mov").unlink()

        retry = await client.post(f"/api/v1/quarantine/{item['asset_id']}/retry", headers=admin)
        assert retry.status_code == 204
        assert (library_root / "Input/Bad/broken.mov").read_bytes() == b"broken"
        assert not qdir.exists()
        assert (await client.get("/api/v1/quarantine")).json() == []
        detail = (await client.get(f"/api/v1/assets/{item['asset_id']}")).json()
        assert detail["status"] == "processing"
        await run_workers(server, worker, library_root)
        await finalize_all(server)
        assert (await client.get(f"/api/v1/assets/{item['asset_id']}")).json()["status"] == "filed"


async def snapshot(server: Server) -> dict[str, Any]:
    async with server.runtime.session_factory() as session:
        assets = (await session.execute(select(Asset).order_by(Asset.id))).scalars().all()
        files = (
            (await session.execute(select(AssetFile).order_by(AssetFile.rel_path))).scalars().all()
        )
        shots = (
            (await session.execute(select(Shot).order_by(Shot.asset_id, Shot.idx))).scalars().all()
        )
        meta = (
            (await session.execute(select(AssetMetadata).order_by(AssetMetadata.asset_id)))
            .scalars()
            .all()
        )
        by_file_id = {f.id: f.rel_path for f in files}
        return {
            "assets": [
                (
                    str(a.id),
                    a.media_type,
                    a.status,
                    a.content_rel_dir,
                    a.codec,
                    a.width,
                    a.camera_model,
                    a.capture_at,
                    a.size_total,
                    a.tech_summary_en,
                    a.title,
                    by_file_id[a.primary_file_id] if a.primary_file_id else None,
                    a.filed_at,
                    a.derived,
                )
                for a in assets
            ],
            "files": [
                (str(f.asset_id), f.role, f.rel_path, f.original_rel_path, f.size, f.blake3)
                for f in files
            ],
            "shots": [(str(s.asset_id), s.idx, s.start_ms, s.keyframes) for s in shots],
            "meta": [(str(m.asset_id), m.normalized, m.raw) for m in meta],
        }


async def test_rebuild_from_sidecars_round_trip(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        await filed_server_state(server, admin, library_root)
        before = await snapshot(server)
        assert len(before["assets"]) == 2
        async with server.runtime.session_factory() as session:
            await session.execute(delete(Asset))
            await session.execute(delete(Batch))
            await session.commit()
        assert (await snapshot(server))["assets"] == []

        async with server.runtime.session_factory() as session:
            preview = await rebuild.rebuild_from_sidecars(session, library_root, dry_run=True)
            await session.rollback()
        assert (preview.sidecars, preview.created) == (2, 2)
        assert (await snapshot(server))["assets"] == []

        for expected_created in (2, 0):  # the second run is a no-op update
            async with server.runtime.session_factory() as session:
                report = await rebuild.rebuild_from_sidecars(session, library_root)
                await session.commit()
            assert report.created == expected_created
            assert report.invalid == report.conflicts == report.missing_files == []
            assert await snapshot(server) == before
        batches = (await server.client.get("/api/v1/batches")).json()
        assert batches[0]["by_status"] == {"filed": 2}


async def test_derived_serving_only_allows_media_extensions_and_sandboxes(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        await filed_server_state(server, admin, library_root)
        client = server.client
        asset = (await client.get("/api/v1/assets?q=clip")).json()["items"][0]
        asset_id = asset["id"]
        derived_dir = library_root / f".rushbase/derived/{asset_id}"
        for name in ("x.html", "x.svg", "x.js", "x.xml", "noext"):
            (derived_dir / name).write_text("<script>alert(1)</script>")
            response = await client.get(f"/api/v1/derived/{asset_id}/{name}")
            assert response.status_code == 404, name
        ok = await client.get(asset["poster_url"])
        assert ok.status_code == 200
        assert ok.headers["content-security-policy"] == "sandbox; default-src 'none'"
        assert ok.headers["x-content-type-options"] == "nosniff"


async def test_every_response_carries_security_headers(
    server_factory: ServerFactory, database_url: str
) -> None:
    async with server_factory(database_url) as server:
        for path in ("/health", "/api/v1/auth/me", "/does-not-exist"):
            response = await server.client.get(path)
            assert "default-src 'self'" in response.headers["content-security-policy"], path
            assert response.headers["x-frame-options"] == "DENY"
            assert response.headers["referrer-policy"] == "same-origin"
            assert response.headers["x-content-type-options"] == "nosniff"
