"""Test helpers that drive assets through the pipeline with fake worker results via the API."""

import os
from pathlib import Path
from typing import Any

import blake3

from rushbase_server.models import Asset
from support import Server

CAPS = {"backends": ["cpu"], "stages": ["probe", "hash", "preview", "shots"], "ram_mb": 8000}
EPOCH = 1_700_000_000


def put_file(root: Path, rel: str, content: bytes) -> None:
    """Create ``rel`` below the library with a fixed old mtime (so it counts as stable)."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (EPOCH, EPOCH))


async def scan(server: Server, rounds: int = 3) -> None:
    """Run scanner passes until files are stable and grouped into assets."""
    for _ in range(rounds):
        await server.runtime.scan_once()
        server.clock.advance(100)


async def enroll(server: Server, admin: dict[str, str]) -> dict[str, str]:
    """Enroll a CPU worker; returns its authorization header."""
    created = await server.client.post(
        "/api/v1/admin/enrollment-tokens", json={"note": "t"}, headers=admin
    )
    response = await server.client.post(
        "/api/v1/worker/enroll",
        json={"token": created.json()["token"], "name": "w", "capabilities": CAPS},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['api_key']}"}


def probe_output(**overrides: Any) -> dict[str, Any]:
    """A plausible probe result."""
    base: dict[str, Any] = {
        "capture_at": "2026-03-09T10:00:00+01:00",
        "capture_tz": "Europe/Berlin",
        "duration_ms": 5000,
        "camera": {"make": "Sony", "model": "ILME-FX3"},
        "lens": {"focal_length_mm": 85},
        "exposure": {"log_profile": "S-Log3", "iso": 800},
        "video": {"codec": "hevc", "width": 3840, "height": 2160, "fps": 25.0},
        "raw": {"ffprobe": {"streams": []}},
    }
    return {**base, **overrides}


def stage_output(root: Path, job: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Valid output for a claimed job (hashes are computed from the real files)."""
    stage = job["stage"]
    if stage in overrides:
        return dict(overrides[stage])
    asset_id = job["asset_id"]
    if stage == "probe":
        return probe_output()
    if stage == "hash":
        files = []
        for item in job["files"]:
            data = (root / item["rel_path"]).read_bytes()
            files.append(
                {
                    "rel_path": item["rel_path"],
                    "size": len(data),
                    "blake3": blake3.blake3(data).hexdigest(),
                }
            )
        return {"files": files}
    if stage == "preview":
        rel = f".rushbase/derived/{asset_id}/poster.jpg"
        put_file(root, rel, b"jpeg")
        return {"files": [{"kind": "poster", "rel_path": rel, "width": 320, "height": 180}]}
    if stage == "shots":
        rel = f".rushbase/derived/{asset_id}/kf0.jpg"
        put_file(root, rel, b"jpeg")
        return {
            "detector": "test",
            "shots": [
                {
                    "idx": 0,
                    "start_ms": 0,
                    "end_ms": 5000,
                    "keyframes": [{"t_ms": 0, "rel_path": rel, "position": "start"}],
                }
            ],
        }
    raise AssertionError(stage)


async def run_workers(
    server: Server,
    worker: dict[str, str],
    root: Path,
    overrides: dict[str, Any] | None = None,
) -> list[str]:
    """Claim and complete every available worker job; returns the stages processed."""
    done: list[str] = []
    while True:
        claim = await server.client.post(
            "/api/v1/worker/claim", json={"max_jobs": 10}, headers=worker
        )
        jobs = claim.json()["jobs"]
        if not jobs:
            return done
        for job in jobs:
            body = {"ok": True, "output": stage_output(root, job, overrides or {})}
            response = await server.client.post(
                f"/api/v1/worker/jobs/{job['job_id']}/result", json=body, headers=worker
            )
            assert response.status_code == 200, response.text
            done.append(job["stage"])


async def finalize_all(server: Server) -> None:
    """Run the maintenance pass (reaper, quarantine, finalize)."""
    server.clock.advance(1)
    await server.runtime.maintain_once()


async def asset_row(server: Server, asset_id: str) -> Asset:
    """Fresh copy of an asset row."""
    from uuid import UUID

    async with server.runtime.session_factory() as session:
        return await session.get_one(Asset, UUID(asset_id))
