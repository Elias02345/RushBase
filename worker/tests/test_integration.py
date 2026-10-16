"""End-to-end: real server + real worker CLI on generated test media (needs PostgreSQL)."""

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from rushbase_core import init_library
from support import PASSWORD, TEST_DATABASE_URL, create_database, drop_database, migrate

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[2]
PORT = 18081
BASE = f"http://127.0.0.1:{PORT}"
STAGES = ("probe", "hash", "preview", "shots")


def wait_until(description: str, check: Any, timeout: float) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {description}")


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[dict[str, Any]]:
    if not TEST_DATABASE_URL:
        pytest.skip("RUSHBASE_TEST_DATABASE_URL not set")
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not installed")
    database = create_database(TEST_DATABASE_URL)
    migrate(database)
    library = tmp_path / "library"
    init_library(library)
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "dev" / "testmedia" / "generate.py"),
            "--library",
            str(library),
        ],
        check=True,
        capture_output=True,
    )
    env = {
        **os.environ,
        "RUSHBASE_DATABASE_URL": database,
        "RUSHBASE_SECRET_KEY": "i" * 40,
        "RUSHBASE_LIBRARY_ROOT": str(library),
        "RUSHBASE_PORT": str(PORT),
        "RUSHBASE_SCAN_INTERVAL_SECONDS": "1",
        "RUSHBASE_STABLE_SCANS": "2",
        "RUSHBASE_STABLE_SECONDS": "2",
        "RUSHBASE_BACKUP_ENABLED": "false",
        "RUSHBASE_LOG_LEVEL": "WARNING",
    }
    server = subprocess.Popen([sys.executable, "-m", "rushbase_server.main"], env=env)
    try:
        wait_until("server", lambda: _healthy(), 40)
        yield {"library": library, "tmp": tmp_path}
    finally:
        server.terminate()
        try:
            server.wait(15)
        except subprocess.TimeoutExpired:
            server.kill()
        drop_database(database)


def _healthy() -> bool:
    try:
        return httpx.get(f"{BASE}/health", timeout=2).status_code in (200, 503)
    except httpx.HTTPError:
        return False


def rushbase(
    args: list[str], env: dict[str, str], timeout: float = 240
) -> subprocess.CompletedProcess[str]:
    exe = str(Path(sys.executable).parent / "rushbase")
    return subprocess.run(
        [exe, *args], env=env, capture_output=True, text=True, timeout=timeout, check=False
    )


def test_worker_processes_testmedia(stack: dict[str, Any]) -> None:
    library: Path = stack["library"]
    tmp: Path = stack["tmp"]
    env = {
        **os.environ,
        "RUSHBASE_DATA_DIR": str(tmp / "worker-data"),
        "RUSHBASE_WORKER_CONFIG": str(tmp / "none.toml"),
        "RUSHBASE_LIBRARY_ROOT": str(library),
        "RUSHBASE_SERVER_URL": BASE,
    }
    for key in ("RUSHBASE_API_TOKEN", "RUSHBASE_API_KEY_FILE", "RUSHBASE_DATABASE_URL"):
        env.pop(key, None)
    with httpx.Client(base_url=BASE, timeout=10) as http:
        setup = http.post("/api/v1/setup", json={"username": "admin", "password": PASSWORD})
        assert setup.status_code == 201, setup.text
        csrf = {"X-CSRF-Token": setup.json()["csrf_token"]}
        token = http.post("/api/v1/admin/enrollment-tokens", json={"note": "it"}, headers=csrf)
        assert token.status_code == 201, token.text

        enrolled = rushbase(
            [
                "enroll",
                "--server",
                BASE,
                "--token",
                token.json()["token"],
                "--name",
                "it",
                "--json",
            ],
            env,
        )
        assert enrolled.returncode == 0, enrolled.stderr + enrolled.stdout
        key_file = tmp / "worker-data" / "api_key"
        assert key_file.stat().st_mode & 0o777 == 0o600

        def assets() -> int:
            return sum(http.get("/api/v1/status").json()["assets"].values())

        wait_until("scanned assets", lambda: assets() >= 8, 90)

        def settled(snapshot: dict[str, Any]) -> bool:
            return all(
                not {"queued", "blocked", "leased"} & set(snapshot["stages"][s]["jobs"])
                for s in STAGES
            )

        deadline = time.monotonic() + 600
        while True:
            run = rushbase(["process", "--exit-when-empty", "--json"], env)
            assert run.returncode == 0, run.stderr + run.stdout
            snapshot = http.get("/api/v1/status").json()
            if settled(snapshot) or time.monotonic() > deadline:
                break
            time.sleep(2)

    events = [json.loads(line)["data"] for line in run.stdout.splitlines() if line.strip()]
    assert events[-1]["event"] == "stopped"
    for stage in STAGES:
        jobs = snapshot["stages"][stage]["jobs"]
        assert jobs.get("done", 0) > 0, (stage, jobs)
        assert "failed" not in jobs, (stage, jobs)
    derived = library / ".rushbase" / "derived"
    assert list(derived.glob("*/poster.webp"))
    assert list(derived.glob("*/proxy.mp4"))
    assert list(derived.glob("*/waveform.json"))
    assert list(derived.glob("*/keyframes/shot-0000-start.jpg"))
    assert not list(derived.rglob(".*.rbtmp*"))
    assert (tmp / "worker-data" / "last_run.json").is_file()
