#!/usr/bin/env python3
"""End-to-end kill test: SIGKILL a worker mid-job and assert the job is re-leased.

Flow: (1) use the server on 127.0.0.1:18080, or start a private fast-timing one on that port
with a throw-away database and library; (2) set up an admin, drop test files into Input/,
wait for jobs; (3) run fake worker A, let it claim a job and heartbeat, kill -9 it;
(4) run fake worker B and assert it receives the very same job (attempt 2) and completes it.

Run through scripts/kill-test.sh (it provides the isolated toolchain and dev credentials).
"""

import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
PORT = 18080
BASE = f"http://127.0.0.1:{PORT}"
PASSWORD = "kill-test-" + secrets.token_urlsafe(12)


def say(message: str) -> None:
    print(f"[kill-test] {message}", flush=True)


def wait_for(description: str, check: Any, timeout: float) -> Any:
    """Poll ``check`` until it returns something truthy, or fail after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.5)
    raise SystemExit(f"[kill-test] FAILED: timed out waiting for {description}")


def server_up() -> bool:
    try:
        return httpx.get(f"{BASE}/health", timeout=2).status_code in (200, 503)
    except httpx.HTTPError:
        return False


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def spawn_worker(mode: str, token: str, state: Path, expect: str = "") -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "scripts" / "fake_worker.py"),
            "--server", BASE,
            "--token", token,
            "--name", f"fake-{mode}",
            "--mode", mode,
            "--state-file", str(state),
            "--expect-job-id", expect,
        ]
    )  # fmt: skip


class PrivateServer:
    """A server on the dev port with short timings, a throw-away DB and library."""

    def __init__(self) -> None:
        base_url = make_url(os.environ["RUSHBASE_DATABASE_URL"])
        self.admin_dsn = base_url.set(
            drivername="postgresql", database="postgres"
        ).render_as_string(hide_password=False)
        self.db_name = f"rb_killtest_{uuid.uuid4().hex[:10]}"
        self.url = base_url.set(database=self.db_name).render_as_string(hide_password=False)
        self.workdir = ROOT / "dev" / "data" / f"kill-test-{uuid.uuid4().hex[:8]}"
        self.library = self.workdir / "library"
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        with psycopg.connect(self.admin_dsn, autocommit=True) as connection:
            connection.execute(f'CREATE DATABASE "{self.db_name}"')
        os.environ["RUSHBASE_DATABASE_URL"] = self.url
        command.upgrade(Config(str(ROOT / "server" / "alembic.ini")), "head")
        self.workdir.mkdir(parents=True)
        env = {
            **os.environ,
            "RUSHBASE_DATABASE_URL": self.url,
            "RUSHBASE_LIBRARY_ROOT": str(self.library),
            "RUSHBASE_PORT": str(PORT),
            "RUSHBASE_SCAN_INTERVAL_SECONDS": "1",
            "RUSHBASE_STABLE_SCANS": "2",
            "RUSHBASE_STABLE_SECONDS": "2",
            "RUSHBASE_JOB_LEASE_SECONDS": "5",
            "RUSHBASE_JOB_HEARTBEAT_SECONDS": "1",
            "RUSHBASE_JOB_BACKOFF_BASE_SECONDS": "1",
            "RUSHBASE_MAINTENANCE_INTERVAL_SECONDS": "1",
            "RUSHBASE_BACKUP_ENABLED": "false",
            "RUSHBASE_LOG_LEVEL": "WARNING",
        }
        self.process = subprocess.Popen([sys.executable, "-m", "rushbase_server.main"], env=env)
        wait_for("the private server", server_up, 30)

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(15)
            except subprocess.TimeoutExpired:
                self.process.kill()
        with psycopg.connect(self.admin_dsn, autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{self.db_name}" WITH (FORCE)')
        shutil.rmtree(self.workdir, ignore_errors=True)


def main() -> int:
    private: PrivateServer | None = None
    workers: list[subprocess.Popen[bytes]] = []
    if server_up():
        say(f"using the running server on {BASE} (default timings: this takes several minutes)")
    else:
        private = PrivateServer()
        say("no server on the dev port: starting a private one with short timings")
        private.start()
    try:
        return run(private, workers)
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
        if private is not None:
            private.stop()


def run(private: PrivateServer | None, workers: list[subprocess.Popen[bytes]]) -> int:
    lease_wait = 150.0 if private is None else 20.0
    with httpx.Client(base_url=BASE, timeout=10) as api:
        status = api.get("/api/v1/setup").json()
        if status["needs_setup"]:
            created = api.post("/api/v1/setup", json={"username": "killtest", "password": PASSWORD})
            created.raise_for_status()
        else:
            user, password = os.environ.get("KILL_TEST_USER"), os.environ.get("KILL_TEST_PASSWORD")
            if not (user and password):
                raise SystemExit("[kill-test] set KILL_TEST_USER and KILL_TEST_PASSWORD (an admin)")
            api.post(
                "/api/v1/auth/login", json={"username": user, "password": password}
            ).raise_for_status()
        csrf = {"X-CSRF-Token": api.cookies.get("rb_csrf") or ""}

        library = private.library if private else Path(os.environ["RUSHBASE_LIBRARY_ROOT"])
        batch = library / "Input" / f"kill-test-{uuid.uuid4().hex[:6]}"
        batch.mkdir(parents=True)
        for index in range(3):
            (batch / f"clip{index}.jpg").write_bytes(os.urandom(2048))
        say(f"created test files in {batch}")

        def probe_queued() -> int:
            stages = api.get("/api/v1/status").json()["stages"]
            return int(stages.get("probe", {}).get("jobs", {}).get("queued", 0))

        wait_for("probe jobs to appear (scanner + stability window)", probe_queued, 240)
        say("probe jobs queued; the backlog is visible without any worker")

        def new_token() -> str:
            response = api.post("/api/v1/admin/enrollment-tokens", json={}, headers=csrf)
            response.raise_for_status()
            return str(response.json()["token"])

        state_a = (private.workdir if private else ROOT / "dev" / "data") / "worker-a.jsonl"
        state_b = state_a.with_name("worker-b.jsonl")
        state_a.unlink(missing_ok=True)
        state_b.unlink(missing_ok=True)

        victim = spawn_worker("hang", new_token(), state_a)
        workers.append(victim)
        claimed = wait_for(
            "worker A to claim a job",
            lambda: next((e for e in read_events(state_a) if e["event"] == "claimed"), None),
            30,
        )
        job_id = claimed["job_id"]
        say(f"worker A (pid {victim.pid}) holds job {job_id}; sending SIGKILL")
        victim.send_signal(signal.SIGKILL)
        victim.wait()

        survivor = spawn_worker("finish", new_token(), state_b, expect=job_id)
        workers.append(survivor)
        say("worker B started; waiting for the lease to expire and the job to be re-leased")
        retaken = wait_for(
            "worker B to receive the same job",
            lambda: next(
                (
                    e
                    for e in read_events(state_b)
                    if e["event"] == "claimed" and e["job_id"] == job_id
                ),
                None,
            ),
            lease_wait + 60,
        )
        assert retaken["attempt"] == 2, f"expected attempt 2, got {retaken}"
        wait_for("worker B to finish", lambda: survivor.poll() == 0, 30)
        say(f"PASSED: job {job_id} was re-leased after SIGKILL (attempt {retaken['attempt']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
