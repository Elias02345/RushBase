"""Shared fixtures for the companion tests: server documents and a mock server transport."""

import json
from typing import Any

import httpx

ASSET_ID = "0b0f6b5e-7c1a-4b53-8a49-5b1c2f3d4e51"
OTHER_ID = "7d3a9a2c-1d52-4a3e-9d57-0c6f0a1b2c3d"


def asset_detail(
    asset_id: str = ASSET_ID, fps: float = 25.0, timecode: str | None = "01:00:00:00"
) -> dict[str, Any]:
    """An ``/api/v1/assets/{id}`` document with two shots and a few tags."""
    return {
        "id": asset_id,
        "media_type": "video",
        "status": "ready",
        "title": None,
        "filename": "A001_C002.mov",
        "duration_ms": 10000,
        "width": 1920,
        "height": 1080,
        "fps": fps,
        "review_state": "pending",
        "summary": None,
        "files": [
            {
                "role": "primary",
                "rel_path": "Content/2026/A001_C002.mov",
                "size": 1,
                "blake3": None,
                "paths": {"linux": "/mnt/lib/Content/2026/A001_C002.mov"},
            }
        ],
        "metadata": {"normalized": {"video": {"timecode": timecode}}},
        "shots": [
            {
                "id": "s0",
                "idx": 0,
                "start_ms": 0,
                "end_ms": 4000,
                "keyframes": [
                    {
                        "t_ms": 2000,
                        "url": f"/api/v1/derived/{asset_id}/kf/0.jpg",
                        "position": "middle",
                    }
                ],
                "ai": {
                    "description_short_de": "Hafen bei Nacht",
                    "description_short_en": "Harbour at night",
                    "shot_size": "wide",
                },
            },
            {
                "id": "s1",
                "idx": 1,
                "start_ms": 4000,
                "end_ms": 10000,
                "keyframes": [],
                "ai": {
                    "description_short_de": "Boot",
                    "description_short_en": "Boat",
                    "shot_size": "close",
                },
            },
        ],
        "ai": {
            "summary_short_de": "Nachtaufnahme im Hafen",
            "summary_short_en": "Night shot in the harbour",
            "tags": [
                {"value": "harbour", "label_de": "Hafen", "label_en": "Harbour"},
                {"value": "night", "label_de": "Nacht", "label_en": "Night"},
            ],
        },
    }


def server_handler(request: httpx.Request) -> httpx.Response:
    """Mock of the user API (search, asset, facets, status, login, token)."""
    path = request.url.path
    auth = request.headers.get("authorization", "")
    if path == "/api/v1/auth/login":
        return httpx.Response(200, json={"user": {}, "csrf_token": "csrf-1"})
    if path == "/api/v1/tokens":
        assert request.headers["x-csrf-token"] == "csrf-1"
        return httpx.Response(201, json={"token": "rbp_" + "x" * 30})
    if path == "/api/v1/auth/logout":
        return httpx.Response(204)
    if not auth.startswith("Bearer "):
        return httpx.Response(401, json={"detail": "authentication required"})
    if path == "/api/v1/usage/report":
        body = json.loads(request.content)
        return httpx.Response(
            200, json={"recorded": len(body["items"]), "cleared": 0, "unknown": 0}
        )
    if path == "/api/v1/search":
        hit = {
            "asset": {"id": ASSET_ID, "filename": "A001_C002.mov", "summary": "Nachtaufnahme"},
            "shot": {"id": "s0", "idx": 0, "start_ms": 0, "end_ms": 4000},
            "score": 0.5,
            "ranks": {},
        }
        return httpx.Response(
            200,
            json={
                "items": [hit],
                "level": "shot",
                "offset": 0,
                "next_offset": None,
                "candidates": 1,
                "channels_ms": {},
                "skipped": [],
            },
        )
    if path == f"/api/v1/assets/{ASSET_ID}":
        return httpx.Response(200, json=asset_detail())
    if path == "/api/v1/assets":
        summary = {"id": ASSET_ID, "filename": "A001_C002.mov", "media_type": "video"}
        return httpx.Response(200, json={"items": [summary], "next_cursor": None, "total": 1})
    if path == "/api/v1/facets":
        return httpx.Response(200, json={"facets": [{"key": "media_type", "kind": "enum"}]})
    if path in {"/api/v1/status", "/api/v1/worker/status"}:
        return httpx.Response(200, json=status_document())
    if path.startswith("/api/v1/derived/"):
        return httpx.Response(200, content=b"\xff\xd8\xff\xd9")
    return httpx.Response(404, json={"detail": "not found"})


def status_document() -> dict[str, Any]:
    """A ``/api/v1/status`` backlog with probe and vision work pending."""

    def stage(jobs: int, files: int, hours: float) -> dict[str, Any]:
        return {
            "jobs": {"queued": jobs},
            "pending_jobs": jobs,
            "pending_files": files,
            "pending_bytes": 0,
            "pending_duration_ms": int(hours * 3_600_000),
            "duration_unknown_jobs": 0,
        }

    return {
        "version": "0.1.0",
        "queue_paused": False,
        "workers_online": 0,
        "workers_total": 1,
        "assets": {},
        "input_files": {},
        "batches": {},
        "stages": {
            "probe": stage(0, 0, 0),
            "preview": stage(40, 40, 3.5),
            "vision": stage(30, 30, 2.5),
        },
    }


def last_json(output: str) -> dict[str, Any]:
    """The JSON document on the last line of CLI output."""
    return dict(json.loads(output.strip().splitlines()[-1]))
