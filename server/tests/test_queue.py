import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select, update

from rushbase_core import Stage, uuid7
from rushbase_server.capabilities import WorkerCapabilities
from rushbase_server.db import SessionFactory
from rushbase_server.models import Asset, AuditLog, Batch, Job, StageResult, Worker
from rushbase_server.services import queue
from support import seed_asset

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
CONFIG = queue.QueueConfig(lease_seconds=120, max_attempts=3, backoff_base_seconds=30)
ALL_CPU = WorkerCapabilities(stages=[Stage.PROBE, Stage.HASH, Stage.PREVIEW, Stage.SHOTS])


async def new_worker(factory: SessionFactory, name: str = "w") -> UUID:
    async with factory() as session:
        worker = Worker(name=name)
        session.add(worker)
        await session.commit()
        return worker.id


async def claim(
    factory: SessionFactory,
    worker_id: UUID,
    now: datetime,
    limit: int = 1,
    caps: WorkerCapabilities = ALL_CPU,
) -> list[queue.ClaimedJob]:
    async with factory() as session:
        worker = await session.get_one(Worker, worker_id)
        claimed = await queue.claim_jobs(session, worker, caps, limit, now, CONFIG)
        await session.commit()
        return claimed


async def finish(
    factory: SessionFactory,
    worker_id: UUID | None,
    job_id: UUID,
    now: datetime,
    data: queue.ResultData | None = None,
) -> queue.ResultOutcome:
    async with factory() as session:
        worker = await session.get(Worker, worker_id) if worker_id else None
        outcome = await queue.submit_result(
            session, job_id, worker, data or queue.ResultData(ok=True), now, CONFIG
        )
        await session.commit()
        return outcome


async def job_state(factory: SessionFactory, job_id: UUID) -> Job:
    async with factory() as session:
        return await session.get_one(Job, job_id)


async def test_dag_blocks_dependents_until_dependencies_finish(
    session_factory: SessionFactory,
) -> None:
    await seed_asset(session_factory)
    worker = await new_worker(session_factory)
    first = await claim(session_factory, worker, T0, limit=5)
    # probe and hash have no dependencies; preview/shots/finalize stay blocked
    assert {c.job.stage for c in first} == {"probe"}  # batched on one stage
    second = await claim(session_factory, worker, T0, limit=5)
    assert {c.job.stage for c in second} == {"hash"}
    assert await claim(session_factory, worker, T0) == []
    await finish(session_factory, worker, first[0].job.id, T0)
    third = await claim(session_factory, worker, T0)
    assert [c.job.stage for c in third] == ["preview"]


async def test_fifo_by_batch_then_asset_and_batch_priority(session_factory: SessionFactory) -> None:
    a1, b1 = await seed_asset(session_factory, stages=("probe", "finalize"))
    a2, _ = await seed_asset(session_factory, b1, stages=("probe", "finalize"))
    async with session_factory() as session:
        later = Batch(
            id=uuid7(),
            input_path="Input/later",
            label="later",
            first_seen_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(later)
        await session.commit()
    a3, _ = await seed_asset(session_factory, later, stages=("probe", "finalize"))
    worker = await new_worker(session_factory)
    order = [c.asset.id for c in await claim(session_factory, worker, T0, limit=3)]
    assert order == [a1.id, a2.id, a3.id]

    # a manual priority moves a later batch to the front
    async with session_factory() as session:
        await session.execute(update(Job).values(state="queued", lease_owner=None))
        await queue.set_batch_priority(session, await session.get_one(Batch, later.id), 10)
        await session.commit()
    first = await claim(session_factory, worker, T0)
    assert first[0].asset.id == a3.id


async def test_stage_preference_drains_one_stage_across_assets(
    session_factory: SessionFactory,
) -> None:
    for _ in range(3):
        await seed_asset(session_factory, stages=("probe", "hash", "finalize"))
    worker = await new_worker(session_factory)
    seen: list[str] = []
    for _ in range(6):
        claimed = await claim(session_factory, worker, T0)
        seen.append(claimed[0].job.stage)
    assert seen == ["probe"] * 3 + ["hash"] * 3  # all probes before any hash


async def test_capabilities_filter_stages_gpu_and_vram(session_factory: SessionFactory) -> None:
    asset, _ = await seed_asset(session_factory, stages=("probe", "finalize"))
    async with session_factory() as session:
        job = (await session.execute(select(Job).where(Job.stage == "probe"))).scalar_one()
        job.requirements = {"gpu": True, "min_vram_mb": 8000}
        await session.commit()
    worker = await new_worker(session_factory)
    cpu = WorkerCapabilities(stages=[Stage.PROBE])
    assert await claim(session_factory, worker, T0, caps=cpu) == []
    small = WorkerCapabilities(stages=[Stage.PROBE], backends=["cuda"], vram_mb=4000)
    assert await claim(session_factory, worker, T0, caps=small) == []
    other_stage = WorkerCapabilities(stages=[Stage.HASH], backends=["cuda"], vram_mb=16000)
    assert await claim(session_factory, worker, T0, caps=other_stage) == []
    big = WorkerCapabilities(stages=[Stage.PROBE], backends=["cuda"], vram_mb=16000)
    claimed = await claim(session_factory, worker, T0, caps=big)
    assert [c.asset.id for c in claimed] == [asset.id]


async def test_finalize_is_never_handed_to_remote_workers(session_factory: SessionFactory) -> None:
    await seed_asset(session_factory, stages=("finalize",))
    worker = await new_worker(session_factory)
    caps = WorkerCapabilities(stages=list(Stage))
    assert await claim(session_factory, worker, T0, caps=caps) == []


async def test_concurrent_claims_never_double_lease(session_factory: SessionFactory) -> None:
    for _ in range(12):
        await seed_asset(session_factory, stages=("probe", "finalize"))
    workers = [await new_worker(session_factory, f"w{i}") for i in range(20)]
    results = await asyncio.gather(*(claim(session_factory, w, T0) for w in workers))
    claimed = [c.job.id for batch in results for c in batch]
    assert len(claimed) == 12  # every job leased...
    assert len(set(claimed)) == 12  # ...exactly once
    async with session_factory() as session:
        leased = await session.scalar(
            select(func.count()).select_from(Job).where(Job.state == "leased")
        )
    assert leased == 12


async def test_kill_test_expired_lease_is_requeued_and_taken_by_second_worker(
    session_factory: SessionFactory,
) -> None:
    await seed_asset(session_factory, stages=("probe", "finalize"))
    w1, w2 = await new_worker(session_factory, "w1"), await new_worker(session_factory, "w2")
    (job,) = await claim(session_factory, w1, T0)
    job_id = job.job.id

    # w1 heartbeats once, then dies silently
    async with session_factory() as session:
        worker1 = await session.get_one(Worker, w1)
        expires = await queue.heartbeat(
            session, job_id, worker1, T0 + timedelta(seconds=30), CONFIG
        )
        await session.commit()
    assert expires == T0 + timedelta(seconds=150)

    async with session_factory() as session:  # not yet expired: nothing to reap
        assert (
            await queue.reap_expired(session, T0 + timedelta(seconds=149), CONFIG)
            == queue.ReapReport()
        )

    late = T0 + timedelta(seconds=151)
    async with session_factory() as session:
        report = await queue.reap_expired(session, late, CONFIG)
        await session.commit()
    assert report == queue.ReapReport(requeued=1, failed=0)
    row = await job_state(session_factory, job_id)
    assert (row.state, row.attempts, row.lease_owner) == ("queued", 1, None)
    assert row.not_before == late + timedelta(seconds=30)  # exponential backoff

    assert await claim(session_factory, w2, late) == []  # still backing off
    (again,) = await claim(session_factory, w2, late + timedelta(seconds=31))
    assert again.job.id == job_id
    assert again.job.lease_owner == w2

    # the dead worker "wakes up": heartbeat and result are refused, the job belongs to w2
    async with session_factory() as session:
        worker1 = await session.get_one(Worker, w1)
        with pytest.raises(queue.LeaseLostError):
            await queue.heartbeat(session, job_id, worker1, late + timedelta(seconds=40), CONFIG)
    with pytest.raises(queue.LeaseLostError):
        await finish(session_factory, w1, job_id, late + timedelta(seconds=40))

    outcome = await finish(session_factory, w2, job_id, late + timedelta(seconds=60))
    assert outcome == queue.ResultOutcome(accepted=True, duplicate=False, job_state="done")


async def test_result_submission_is_idempotent(session_factory: SessionFactory) -> None:
    asset, _ = await seed_asset(session_factory, stages=("probe", "finalize"))
    worker = await new_worker(session_factory)
    (job,) = await claim(session_factory, worker, T0)
    data = queue.ResultData(
        ok=True, output={"duration_ms": 5}, model_id="ffprobe", model_version="7"
    )
    first = await finish(session_factory, worker, job.job.id, T0, data)
    second = await finish(session_factory, worker, job.job.id, T0, data)
    assert first.accepted
    assert not first.duplicate
    assert second == queue.ResultOutcome(accepted=False, duplicate=True, job_state="done")
    async with session_factory() as session:
        results = (
            (await session.execute(select(StageResult).where(StageResult.asset_id == asset.id)))
            .scalars()
            .all()
        )
    assert len(results) == 1
    assert results[0].output == {"duration_ms": 5}
    assert results[0].model_version == "7"


async def test_max_attempts_fail_the_job_and_record_quarantine_intent(
    session_factory: SessionFactory,
) -> None:
    asset, _ = await seed_asset(
        session_factory, stages=("probe", "hash", "finalize"), config=CONFIG
    )
    worker = await new_worker(session_factory)
    now = T0
    for attempt in range(1, 4):
        (job,) = await claim(session_factory, worker, now)
        assert job.job.stage == "probe"
        outcome = await finish(
            session_factory, worker, job.job.id, now, queue.ResultData(ok=False, error="corrupt")
        )
        now += timedelta(hours=1)
        assert outcome.job_state == ("queued" if attempt < 3 else "failed")
    async with session_factory() as session:
        stored = await session.get_one(Asset, asset.id)
        jobs = {
            j.stage: j
            for j in (await session.execute(select(Job).where(Job.asset_id == asset.id))).scalars()
        }
        audit = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "asset.quarantine_intent")
                )
            )
            .scalars()
            .all()
        )
        failures = await session.scalar(
            select(func.count()).select_from(StageResult).where(StageResult.ok.is_(False))
        )
    assert stored.status == "quarantined"
    assert (jobs["probe"].state, jobs["probe"].attempts) == ("failed", 3)
    assert (jobs["hash"].state, jobs["finalize"].state) == ("cancelled", "cancelled")
    assert [a.target_id for a in audit] == [str(asset.id)]
    assert failures == 3
    assert await claim(session_factory, worker, now) == []  # nothing left for quarantined assets


async def test_expired_leases_count_towards_max_attempts(session_factory: SessionFactory) -> None:
    asset, _ = await seed_asset(session_factory, stages=("probe", "finalize"), config=CONFIG)
    worker = await new_worker(session_factory)
    now = T0
    for _ in range(3):
        await claim(session_factory, worker, now)
        now += timedelta(hours=2)
        async with session_factory() as session:
            await queue.reap_expired(session, now, CONFIG)
            await session.commit()
        now += timedelta(hours=1)  # past the backoff
    async with session_factory() as session:
        assert (await session.get_one(Asset, asset.id)).status == "quarantined"


async def test_non_retryable_error_fails_immediately(session_factory: SessionFactory) -> None:
    await seed_asset(session_factory, stages=("probe", "finalize"))
    worker = await new_worker(session_factory)
    (job,) = await claim(session_factory, worker, T0)
    outcome = await finish(
        session_factory,
        worker,
        job.job.id,
        T0,
        queue.ResultData(ok=False, error="x", retryable=False),
    )
    assert outcome.job_state == "failed"


async def test_release_gives_the_job_back_without_penalty(session_factory: SessionFactory) -> None:
    await seed_asset(session_factory, stages=("probe", "finalize"))
    w1, w2 = await new_worker(session_factory, "w1"), await new_worker(session_factory, "w2")
    (job,) = await claim(session_factory, w1, T0)
    async with session_factory() as session:
        await queue.release_job(session, job.job.id, await session.get_one(Worker, w1), T0)
        await session.commit()
    row = await job_state(session_factory, job.job.id)
    assert (row.state, row.attempts, row.not_before) == ("queued", 0, None)
    assert [c.job.id for c in await claim(session_factory, w2, T0)] == [job.job.id]


async def test_pause_global_and_per_batch(session_factory: SessionFactory) -> None:
    asset, batch = await seed_asset(session_factory, stages=("probe", "finalize"))
    worker = await new_worker(session_factory)
    async with session_factory() as session:
        await queue.set_paused(session, True)
        await session.commit()
    assert await claim(session_factory, worker, T0) == []
    async with session_factory() as session:
        await queue.set_paused(session, False)
        await queue.set_batch_paused(session, await session.get_one(Batch, batch.id), True)
        await session.commit()
    assert await claim(session_factory, worker, T0) == []
    async with session_factory() as session:
        await queue.set_batch_paused(session, await session.get_one(Batch, batch.id), False)
        await session.commit()
    assert [c.asset.id for c in await claim(session_factory, worker, T0)] == [asset.id]


async def test_server_stage_finalize_completes_after_everything_else(
    session_factory: SessionFactory,
) -> None:
    asset, batch = await seed_asset(session_factory, stages=("probe", "finalize"))
    worker = await new_worker(session_factory)
    async with session_factory() as session:
        assert await queue.run_server_stages(session, T0) == 0  # blocked behind probe
    (job,) = await claim(session_factory, worker, T0)
    await finish(session_factory, worker, job.job.id, T0)
    async with session_factory() as session:
        assert await queue.run_server_stages(session, T0) == 1
        await session.commit()
    async with session_factory() as session:
        assert (await session.get_one(Asset, asset.id)).status == "review"
        assert (await session.get_one(Batch, batch.id)).status == "done"


async def test_reprocess_requeues_only_assets_with_older_model_versions(
    session_factory: SessionFactory,
) -> None:
    old, batch = await seed_asset(session_factory, stages=("probe", "finalize"))
    new, _ = await seed_asset(session_factory, batch, stages=("probe", "finalize"))
    worker = await new_worker(session_factory)
    versions = {old.id: "1.9", new.id: "2.0"}
    for _ in range(2):
        (job,) = await claim(session_factory, worker, T0)
        await finish(
            session_factory,
            worker,
            job.job.id,
            T0,
            queue.ResultData(ok=True, model_version=versions[job.asset.id]),
        )
    async with session_factory() as session:
        touched = await queue.reprocess_stage(session, Stage.PROBE, "2.0", T0, CONFIG)
        again = await queue.reprocess_stage(
            session, Stage.PROBE, "2.0", T0, CONFIG
        )  # already queued
        await session.commit()
    assert touched == [old.id]
    assert again == []
    (job,) = await claim(session_factory, worker, T0)
    assert job.asset.id == old.id
    assert job.upstream == {"probe": {}}


async def test_backlog_reports_files_bytes_and_duration_without_any_worker(
    session_factory: SessionFactory,
) -> None:
    from rushbase_server.services import stats

    await seed_asset(session_factory, stages=("probe", "finalize"), size=500, duration_ms=2000)
    await seed_asset(session_factory, stages=("probe", "finalize"), size=300)
    async with session_factory() as session:
        backlog = await stats.stage_backlog(session)
    probe = backlog["probe"]
    assert probe.pending_jobs == 2
    assert probe.pending_files == 2
    assert probe.pending_bytes == 800
    assert probe.pending_duration_ms == 2000
    assert probe.duration_unknown_jobs == 1
    assert probe.jobs == {"queued": 2}
    assert backlog["finalize"].jobs == {"blocked": 2}
