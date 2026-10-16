import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from blake3 import blake3
from pydantic import BaseModel

from rushbase_core import Stage, init_library
from rushbase_core.results import HashOutput, parse_stage_output
from rushbase_worker.client import (
    ApiError,
    AuthError,
    LeaseLostError,
    UnavailableError,
    WorkerClient,
    backoff_delays,
)
from rushbase_worker.config import WorkerSettings
from rushbase_worker.models import Claim, Job, JobFile, LibraryInfo, ResultAck
from rushbase_worker.mount import MountError, verify_mount
from rushbase_worker.runner import Runner, RunOptions, parse_until
from rushbase_worker.stages import HANDLERS, Requirements, StageContext, StageError
from rushbase_worker.stages.hash import hash_file
from rushbase_worker.tools.toolbox import Toolbox


def make_job(stage: Stage = Stage.HASH, rel: str = "Input/a.bin") -> Job:
    return Job(
        job_id=uuid4(),
        asset_id=uuid4(),
        stage=stage,
        media_type="other",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        files=[JobFile(role="primary", rel_path=rel, size=3)],
    )


class FakeApi:
    def __init__(self, batches: list[list[Job]]) -> None:
        self.batches = batches
        self.results: dict[UUID, dict[str, Any]] = {}
        self.released: list[UUID] = []
        self.heartbeats: list[UUID] = []
        self.lose_lease = False
        self.claims = 0

    async def library(self) -> LibraryInfo:
        return LibraryInfo(library_id=uuid4())

    async def claim(self, max_jobs: int, capabilities: dict[str, Any]) -> Claim:
        self.claims += 1
        jobs = self.batches.pop(0)[:max_jobs] if self.batches else []
        return Claim(jobs=jobs, heartbeat_seconds=0.01, lease_seconds=1)

    async def heartbeat(self, job_id: UUID) -> None:
        self.heartbeats.append(job_id)
        if self.lose_lease:
            raise LeaseLostError(409, "lease_lost")

    async def result(self, job_id: UUID, body: dict[str, Any]) -> ResultAck:
        self.results[job_id] = body
        return ResultAck(accepted=True, duplicate=False, job_state="done")

    async def release(self, job_id: UUID) -> None:
        self.released.append(job_id)


class StubHandler:
    stage = Stage.HASH
    requirements = Requirements()

    def __init__(self, delay: float = 0.0, error: Exception | None = None) -> None:
        self.delay = delay
        self.error = error
        self.started = asyncio.Event()

    async def run(self, job: Job, ctx: StageContext) -> BaseModel:
        self.started.set()
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return HashOutput(files=[])


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    settings = WorkerSettings(_env_file=None)
    return StageContext(settings, Toolbox(settings), tmp_path)


def runner_for(api: FakeApi, ctx: StageContext, handler: StubHandler, **opts: Any) -> Runner:
    options = RunOptions(
        concurrency=opts.pop("concurrency", 2),
        heartbeat_seconds=0.01,
        poll_interval_seconds=0.02,
        **opts,
    )
    return Runner(api, ctx, {Stage.HASH: handler}, options)


async def test_exit_when_empty_reports_results(ctx: StageContext) -> None:
    jobs = [make_job(), make_job()]
    api = FakeApi([jobs])
    summary = await runner_for(api, ctx, StubHandler(0.03), exit_when_empty=True).run()
    assert summary.reason == "empty"
    assert summary.completed == 2
    assert all(api.results[j.job_id]["ok"] for j in jobs)
    assert api.heartbeats  # heartbeats ran while the handlers worked
    parse_stage_output(Stage.HASH, api.results[jobs[0].job_id]["output"])  # contract-valid


async def test_stage_error_is_reported_with_retryable_flag(ctx: StageContext) -> None:
    job = make_job()
    api = FakeApi([[job]])
    handler = StubHandler(error=StageError("broken file", retryable=False))
    summary = await runner_for(api, ctx, handler, exit_when_empty=True).run()
    body = api.results[job.job_id]
    assert (body["ok"], body["retryable"], body["error"]) == (False, False, "broken file")
    assert summary.failed == 1


async def test_crash_is_retryable_failure(ctx: StageContext) -> None:
    job = make_job()
    api = FakeApi([[job]])
    await runner_for(api, ctx, StubHandler(error=RuntimeError("boom")), exit_when_empty=True).run()
    assert api.results[job.job_id]["retryable"] is True
    assert "boom" in api.results[job.job_id]["error"]


async def test_stop_releases_in_flight_jobs(ctx: StageContext) -> None:
    job = make_job()
    api = FakeApi([[job]])
    handler = StubHandler(delay=30)
    runner = runner_for(api, ctx, handler)
    task = asyncio.create_task(runner.run())
    await asyncio.wait_for(handler.started.wait(), 2)
    runner.request_stop("signal")
    summary = await asyncio.wait_for(task, 5)
    assert api.released == [job.job_id]
    assert job.job_id not in api.results
    assert summary.released == 1
    assert summary.reason == "signal"


async def test_stop_time_releases_in_flight_jobs(ctx: StageContext) -> None:
    job = make_job()
    api = FakeApi([[job]])
    handler = StubHandler(delay=30)
    deadline = datetime.now(UTC) + timedelta(milliseconds=150)
    summary = await asyncio.wait_for(runner_for(api, ctx, handler, until=deadline).run(), 5)
    assert summary.reason == "stop_time"
    assert api.released == [job.job_id]


async def test_lost_lease_discards_result_without_release(ctx: StageContext) -> None:
    job = make_job()
    api = FakeApi([[job]])
    api.lose_lease = True
    summary = await runner_for(api, ctx, StubHandler(delay=5), exit_when_empty=True).run()
    assert summary.lease_lost == 1
    assert job.job_id not in api.results
    assert api.released == []


async def test_concurrency_is_bounded(ctx: StageContext) -> None:
    running = 0
    peak = 0

    class Counting(StubHandler):
        async def run(self, job: Job, ctx: StageContext) -> BaseModel:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.03)
            running -= 1
            return HashOutput(files=[])

    api = FakeApi([[make_job()] for _ in range(6)])
    summary = await runner_for(api, ctx, Counting(), concurrency=2, exit_when_empty=True).run()
    assert summary.completed == 6
    assert peak <= 2


async def test_hash_stage_matches_blake3(tmp_path: Path) -> None:
    init_library(tmp_path)
    (tmp_path / "Input" / "a.bin").write_bytes(b"abc")
    settings = WorkerSettings(_env_file=None)
    out = await HANDLERS[Stage.HASH].run(
        make_job(), StageContext(settings, Toolbox(settings), tmp_path)
    )
    assert isinstance(out, HashOutput)
    assert (out.files[0].size, out.files[0].blake3) == (3, blake3(b"abc").hexdigest())
    assert hash_file(tmp_path / "Input" / "a.bin") == (3, blake3(b"abc").hexdigest())


def test_parse_until() -> None:
    now = datetime(2026, 10, 16, 22, 0).astimezone()
    assert parse_until("23:30", now).hour == 23
    assert parse_until("07:00", now).date() == (now + timedelta(days=1)).date()
    with pytest.raises(ValueError, match="HH:MM"):
        parse_until("noon")


def test_mount_check(tmp_path: Path) -> None:
    manifest = init_library(tmp_path)
    assert verify_mount(tmp_path, manifest.library_id, None) == manifest.library_id
    with pytest.raises(MountError):
        verify_mount(tmp_path, uuid4(), None)
    with pytest.raises(MountError):
        verify_mount(None, manifest.library_id, None)
    with pytest.raises(MountError):
        verify_mount(tmp_path / "missing", manifest.library_id, None)


async def test_client_retries_then_succeeds() -> None:
    calls = 0
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"library_id": str(uuid4()), "name": "x"})

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    async with WorkerClient(
        "http://srv", "k", transport=httpx.MockTransport(handler), sleep=sleep
    ) as client:
        info = await client.library()
    assert info.name == "x"
    assert calls == 3
    assert len(slept) == 2


async def test_client_error_mapping() -> None:
    codes = iter([401, 409, 500])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(codes), json={"detail": "nope"})

    async with WorkerClient("http://srv", "k", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AuthError):
            await client.library()
        with pytest.raises(LeaseLostError):
            await client.heartbeat(uuid4())
        with pytest.raises(ApiError):
            await client.release(uuid4())


async def test_client_gives_up() -> None:
    async def sleep(_: float) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    async with WorkerClient(
        "http://srv", "k", retries=2, transport=httpx.MockTransport(handler), sleep=sleep
    ) as client:
        with pytest.raises(UnavailableError):
            await client.library()


def test_backoff_grows_and_caps() -> None:
    delays = backoff_delays(8, base=1.0, cap=10.0)
    assert delays[0] < delays[2]
    assert max(delays) <= 12.5
