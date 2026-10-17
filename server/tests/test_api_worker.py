import asyncio
import os
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from support import Server

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]
CAPS = {"backends": ["cpu"], "stages": ["probe", "hash", "preview", "shots"], "ram_mb": 8000}


async def enroll(server: Server, admin: dict[str, str], name: str = "gpu-pc") -> dict[str, str]:
    created = await server.client.post(
        "/api/v1/admin/enrollment-tokens", json={"note": name}, headers=admin
    )
    assert created.status_code == 201
    response = await server.client.post(
        "/api/v1/worker/enroll",
        json={"token": created.json()["token"], "name": name, "capabilities": CAPS},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['api_key']}"}


def put_photo(root: Path, name: str) -> None:
    path = root / "Input" / "shoot" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 64)
    os.utime(path, (1_700_000_000, 1_700_000_000))


async def ingest(server: Server, names: list[str]) -> None:
    root = server.runtime.binding.root
    assert root is not None
    for name in names:
        put_photo(root, name)
    for _ in range(3):
        await server.runtime.scan_once()
        server.clock.advance(100)


async def test_enroll_claim_heartbeat_result_flow(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        await ingest(server, ["a.jpg", "b.jpg"])
        worker = await enroll(server, admin)
        client = server.client
        client.cookies.clear()

        lib = (await client.get("/api/v1/worker/library", headers=worker)).json()
        assert lib["library_id"] == str(server.runtime.binding.library_id)

        claim = await client.post("/api/v1/worker/claim", json={"max_jobs": 5}, headers=worker)
        assert claim.status_code == 200
        jobs = claim.json()["jobs"]
        assert len(jobs) == 2
        assert {j["stage"] for j in jobs} == {"probe"}  # batched on one stage
        job = jobs[0]
        assert job["files"][0]["rel_path"].startswith("Input/shoot/")
        assert job["attempt"] == 1

        beat = await client.post(f"/api/v1/worker/jobs/{job['job_id']}/heartbeat", headers=worker)
        assert beat.status_code == 200
        assert datetime.fromisoformat(
            beat.json()["lease_expires_at"]
        ) > server.clock.now().astimezone(UTC)

        body = {
            "ok": True,
            "output": {"duration_ms": 0},
            "model_id": "ffprobe",
            "model_version": "7.0",
            "duration_ms": 12,
        }
        first = await client.post(
            f"/api/v1/worker/jobs/{job['job_id']}/result", json=body, headers=worker
        )
        assert first.json() == {"accepted": True, "duplicate": False, "job_state": "done"}
        again = await client.post(
            f"/api/v1/worker/jobs/{job['job_id']}/result", json=body, headers=worker
        )
        assert again.status_code == 200
        assert again.json() == {"accepted": False, "duplicate": True, "job_state": "done"}

        # no probe is left to claim (the other one is leased); other stages follow
        nxt = (
            await client.post("/api/v1/worker/claim", json={"max_jobs": 1}, headers=worker)
        ).json()
        assert nxt["jobs"][0]["stage"] in {"hash", "preview"}
        other = nxt["jobs"][0]["job_id"]
        release = await client.post(f"/api/v1/worker/jobs/{other}/release", headers=worker)
        assert release.status_code == 204
        refused = await client.post(f"/api/v1/worker/jobs/{other}/heartbeat", headers=worker)
        assert refused.status_code == 409
        assert "lease_lost" in refused.json()["detail"]


async def test_enrollment_token_is_single_use_and_keys_are_validated(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        client = server.client
        token = (
            await client.post("/api/v1/admin/enrollment-tokens", json={}, headers=admin)
        ).json()["token"]
        first = await client.post("/api/v1/worker/enroll", json={"token": token, "name": "w1"})
        assert first.status_code == 201
        assert first.json()["api_key"].startswith("rbw_")
        reuse = await client.post("/api/v1/worker/enroll", json={"token": token, "name": "w2"})
        assert reuse.status_code == 403
        assert (
            await client.post(
                "/api/v1/worker/enroll", json={"token": "rbe_" + "x" * 40, "name": "w"}
            )
        ).status_code == 403

        client.cookies.clear()
        assert (await client.post("/api/v1/worker/claim", json={})).status_code == 401
        garbage = {"Authorization": "Bearer rbw_garbage"}
        assert (
            await client.post("/api/v1/worker/claim", json={}, headers=garbage)
        ).status_code == 401
        personal = {"Authorization": "Bearer rbp_whatever"}
        assert (
            await client.post("/api/v1/worker/claim", json={}, headers=personal)
        ).status_code == 401


async def test_enrollment_token_expires(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        token = (
            await server.client.post(
                "/api/v1/admin/enrollment-tokens", json={"ttl_hours": 1}, headers=admin
            )
        ).json()["token"]
        server.clock.advance(3601)
        response = await server.client.post(
            "/api/v1/worker/enroll", json={"token": token, "name": "late"}
        )
        assert response.status_code == 403


async def test_revoked_worker_key_stops_working_and_workers_are_listed(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        worker = await enroll(server, admin)
        listing = (await server.client.get("/api/v1/admin/workers")).json()
        assert [w["name"] for w in listing] == ["gpu-pc"]
        assert listing[0]["online"] is True
        revoke = await server.client.post(
            f"/api/v1/admin/workers/{listing[0]['id']}/revoke", headers=admin
        )
        assert revoke.status_code == 204
        response = await server.client.post("/api/v1/worker/claim", json={}, headers=worker)
        assert response.status_code == 401
        server.clock.advance(1000)
        assert (await server.client.get("/api/v1/admin/workers")).json()[0]["online"] is False


async def test_status_reports_backlog_without_any_worker_and_hash_runs_in_process(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(
        database_url, library_root=library_root, inprocess_stages="hash"
    ) as server:
        await server.setup_admin()
        await ingest(server, ["a.jpg", "b.jpg", "c.jpg"])
        status = (await server.client.get("/api/v1/status")).json()
        assert status["workers_online"] == 0
        probe = status["stages"]["probe"]
        assert probe["pending_jobs"] == 3
        assert probe["pending_files"] == 3
        assert probe["pending_bytes"] == 192
        assert probe["jobs"] == {"queued": 3}
        assert status["assets"] == {"processing": 3}
        assert status["input_files"] == {"grouped": 3}

        # no worker is online, yet the optional in-process worker drains `hash`
        for _ in range(3):
            await server.runtime.maintain_once()
        after = (await server.client.get("/api/v1/status")).json()
        assert after["stages"]["hash"]["jobs"] == {"done": 3}
        assert after["stages"]["probe"]["jobs"] == {"queued": 3}


async def test_pause_blocks_claims_and_metrics_are_opt_in(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(
        database_url,
        library_root=library_root,
        metrics_enabled=True,
        metrics_token=SecretStr("m" * 20),
    ) as server:
        admin = await server.setup_admin()
        await ingest(server, ["a.jpg"])
        worker = await enroll(server, admin)
        assert (
            await server.client.post("/api/v1/admin/queue/pause", headers=admin)
        ).status_code == 204
        paused = await server.client.post("/api/v1/worker/claim", json={}, headers=worker)
        assert paused.json()["jobs"] == []
        assert (await server.client.get("/api/v1/status")).json()["queue_paused"] is True
        assert (
            await server.client.post("/api/v1/admin/queue/resume", headers=admin)
        ).status_code == 204
        resumed = await server.client.post("/api/v1/worker/claim", json={}, headers=worker)
        assert len(resumed.json()["jobs"]) == 1
        assert (await server.client.get("/metrics")).status_code == 401
        metrics = await server.client.get(
            "/metrics", headers={"Authorization": "Bearer " + "m" * 20}
        )
        assert metrics.status_code == 200
        assert 'rushbase_jobs{stage="probe",state="leased"} 1' in metrics.text
    async with server_factory(database_url, library_root=library_root) as off:
        assert (await off.client.get("/metrics")).status_code == 404


async def test_concurrent_http_claims_never_double_lease(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        await ingest(server, [f"p{i}.jpg" for i in range(8)])
        workers = [await enroll(server, admin, f"w{i}") for i in range(20)]
        server.client.cookies.clear()

        async def claim(headers: dict[str, str]) -> httpx.Response:
            return await server.client.post("/api/v1/worker/claim", json={}, headers=headers)

        responses = await asyncio.gather(*(claim(h) for h in workers))
        ids = [j["job_id"] for r in responses for j in r.json()["jobs"]]
        assert len(ids) == 16  # 8 assets x (probe + hash), 20 claimers
        assert len(set(ids)) == 16


async def test_workers_read_the_backlog_with_their_own_key(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        await ingest(server, ["a.jpg", "b.jpg"])
        worker = await enroll(server, admin)
        server.client.cookies.clear()
        response = await server.client.get("/api/v1/worker/status", headers=worker)
        assert response.status_code == 200, response.text
        assert response.json()["stages"]["probe"]["pending_jobs"] == 2
        # a worker key is not a user session: the user endpoint stays closed to it
        assert (await server.client.get("/api/v1/status", headers=worker)).status_code == 401
        assert (await server.client.get("/api/v1/worker/status")).status_code == 401
