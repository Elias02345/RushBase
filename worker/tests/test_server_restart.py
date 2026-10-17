"""A worker rides out a restart of the real server process (needs PostgreSQL)."""

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from rushbase_core import init_library
from rushbase_worker.client import WorkerClient
from support import PASSWORD, TEST_DATABASE_URL, create_database, drop_database, migrate

pytestmark = pytest.mark.postgres

PORT = 18085
BASE = f"http://127.0.0.1:{PORT}"


def _healthy() -> bool:
    try:
        return httpx.get(f"{BASE}/health", timeout=1).status_code in (200, 503)
    except httpx.HTTPError:
        return False


def _wait(description: str, check: Any, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.3)
    raise AssertionError(f"timed out waiting for {description}")


class Stack:
    """A server subprocess on a throw-away database that can be restarted."""

    def __init__(self, env: dict[str, str]) -> None:
        self._env = env
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-m", "rushbase_server.main"], env=self._env
        )
        _wait("server", _healthy, 40)

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(15)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process = None


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[Stack]:
    if not TEST_DATABASE_URL:
        pytest.skip("RUSHBASE_TEST_DATABASE_URL not set")
    database = create_database(TEST_DATABASE_URL)
    migrate(database)
    library = tmp_path / "library"
    init_library(library)
    media = library / "Input" / "Day"
    media.mkdir(parents=True)
    (media / "clip.mov").write_bytes(b"video-bytes")
    env = {
        **os.environ,
        "RUSHBASE_DATABASE_URL": database,
        "RUSHBASE_SECRET_KEY": "r" * 40,
        "RUSHBASE_LIBRARY_ROOT": str(library),
        "RUSHBASE_PORT": str(PORT),
        "RUSHBASE_SCAN_INTERVAL_SECONDS": "1",
        "RUSHBASE_STABLE_SCANS": "1",
        "RUSHBASE_STABLE_SECONDS": "0",
        "RUSHBASE_BACKUP_ENABLED": "false",
        "RUSHBASE_LOG_LEVEL": "WARNING",
    }
    server = Stack(env)
    server.start()
    try:
        yield server
    finally:
        server.stop()
        drop_database(database)


async def test_worker_client_reconnects_after_a_server_restart(stack: Stack) -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=10) as http:
        setup = await http.post("/api/v1/setup", json={"username": "admin", "password": PASSWORD})
        csrf = {"X-CSRF-Token": setup.json()["csrf_token"]}
        token = (
            await http.post("/api/v1/admin/enrollment-tokens", json={"note": "r"}, headers=csrf)
        ).json()["token"]
    enrolled = await WorkerClient.enroll(
        BASE, {"token": token, "name": "restart-test", "capabilities": {}}
    )
    async with WorkerClient(BASE, enrolled.api_key, retries=10) as client:
        jobs: list[Any] = []
        for _ in range(60):
            jobs = (await client.claim(1, {"stages": ["probe"]})).jobs
            if jobs:
                break
            await asyncio.sleep(0.5)
        assert jobs, "the scanner never produced a job"
        job_id = jobs[0].job_id
        await client.heartbeat(job_id)

        stack.stop()  # the server is gone: calls must keep retrying instead of failing
        pending = asyncio.create_task(client.heartbeat(job_id))
        await asyncio.sleep(3)
        assert not pending.done()
        await asyncio.to_thread(stack.start)

        await asyncio.wait_for(pending, 60)  # same lease, still ours after the restart
        await client.release(job_id)
        again = (await client.claim(1, {"stages": ["probe"]})).jobs
        assert [j.job_id for j in again] == [job_id]
