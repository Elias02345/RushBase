"""The claim loop: lease jobs, run stage handlers, heartbeat, report, release on shutdown."""

import asyncio
import contextlib
import logging
import os
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError

from rushbase_core import DEFAULT_HEARTBEAT_SECONDS, Stage
from rushbase_worker import get_version
from rushbase_worker.client import ApiError, AuthError, LeaseLostError, UnavailableError, WorkerApi
from rushbase_worker.models import Job
from rushbase_worker.runtimes.manager import Runtimes
from rushbase_worker.stages import StageContext, StageError, StageHandler

log = logging.getLogger(__name__)

Event = dict[str, Any]
EventSink = Callable[[Event], None]
MAX_ERROR_CHARS = 4900


@dataclass(frozen=True)
class RunOptions:
    """How a run behaves."""

    concurrency: int
    heartbeat_seconds: float | None = None
    poll_interval_seconds: float = 5.0
    claim_batch: int | None = None
    exit_when_empty: bool = False
    until: datetime | None = None


@dataclass
class RunSummary:
    """Counters of a finished run."""

    reason: str = "stopped"
    completed: int = 0
    failed: int = 0
    released: int = 0
    lease_lost: int = 0
    by_stage: dict[str, int] = field(default_factory=dict)


def parse_until(value: str, now: datetime | None = None) -> datetime:
    """Next local occurrence of ``HH:MM`` (today if still ahead, otherwise tomorrow)."""
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
        reference = (now or datetime.now().astimezone()).astimezone()
        target = reference.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError as exc:
        raise ValueError(f"expected HH:MM, got {value!r}") from exc
    if target <= reference:
        target += timedelta(days=1)
    return target


def system_ram_mb() -> int | None:
    """Physical RAM in MiB where the platform exposes it."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024)
    except (ValueError, OSError, AttributeError):
        return None


def capabilities_for(stages: list[Stage], runtimes: Runtimes | None = None) -> dict[str, Any]:
    """The capability document sent with every claim (hardware facts when AI runtimes exist)."""
    document: dict[str, Any] = {
        "backends": ["cpu"],
        "stages": [s.value for s in stages],
        "ram_mb": system_ram_mb(),
        "profile": "cpu",
    }
    if runtimes is not None:
        hardware = runtimes.hardware
        document["backends"] = list(dict.fromkeys([hardware.backend.value, "cpu"]))
        document["profile"] = hardware.profile.value
        document["vram_mb"] = hardware.effective_vram_mb
    return document


def failure_body(error: str, retryable: bool, started: datetime, began: float) -> dict[str, Any]:
    """Result payload for a failed job."""
    return {
        "ok": False,
        "error": error[:MAX_ERROR_CHARS],
        "retryable": retryable,
        "started_at": started.isoformat(),
        "duration_ms": round((time.monotonic() - began) * 1000),
    }


class Runner:
    """Runs the claim loop until stopped, out of work (optionally) or past the stop time."""

    def __init__(
        self,
        api: WorkerApi,
        ctx: StageContext,
        handlers: dict[Stage, StageHandler],
        options: RunOptions,
        emit: EventSink = lambda _event: None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._api = api
        self._ctx = ctx
        self._handlers = handlers
        self._options = options
        self._emit = emit
        self._clock = clock
        self._sleep = sleep
        self._stop = asyncio.Event()
        self._stop_reason = "stopped"
        self._lease_lost: set[UUID] = set()
        self._heartbeat = options.heartbeat_seconds or DEFAULT_HEARTBEAT_SECONDS
        self.summary = RunSummary()

    def request_stop(self, reason: str = "signal") -> None:
        """Ask the loop to stop; in-flight jobs are cancelled and released."""
        if not self._stop.is_set():
            self._stop_reason = reason
            self._stop.set()

    def install_signal_handlers(self) -> None:
        """Stop gracefully on SIGINT and SIGTERM (POSIX event loops only)."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, self.request_stop, sig.name.lower())

    async def run(self) -> RunSummary:
        """Run until a stop condition; returns the counters."""
        opts = self._options
        stages = list(self._handlers)
        self._emit({"event": "started", "stages": [s.value for s in stages],
                    "concurrency": opts.concurrency, "version": get_version()})  # fmt: skip
        active: set[asyncio.Task[None]] = set()
        reason = "stopped"
        try:
            while True:
                if self._stop.is_set():
                    reason = self._stop_reason
                    break
                if opts.until is not None and self._clock() >= opts.until:
                    reason = "stop_time"
                    break
                if len(active) < opts.concurrency:
                    claimed = await self._claim(opts.concurrency - len(active), stages)
                    for job in claimed:
                        active.add(asyncio.create_task(self._process(job)))
                    if not claimed and not active and self._ctx.runtimes is not None:
                        await self._ctx.runtimes.lifecycle.unload_idle()  # free the GPU while idle
                    if not claimed and not active and opts.exit_when_empty:
                        reason = "empty"
                        break
                    if claimed and len(active) < opts.concurrency:
                        continue  # try to fill the remaining slots before waiting
                await self._wait(active)
                active = {t for t in active if not t.done()}
        finally:
            await self._shutdown(active)
            if self._ctx.runtimes is not None:
                await self._ctx.runtimes.close()
        self.summary.reason = reason
        self._emit({"event": "stopped", "reason": reason, **vars(self.summary)})
        return self.summary

    async def _claim(self, free: int, stages: list[Stage]) -> list[Job]:
        batch = min(free, self._options.claim_batch or free)
        try:
            claim = await self._api.claim(batch, capabilities_for(stages, self._ctx.runtimes))
        except AuthError:
            raise
        except (UnavailableError, ApiError) as exc:
            log.warning("claim failed: %s", exc)
            self._emit({"event": "claim_failed", "error": str(exc)})
            return []
        if self._options.heartbeat_seconds is None:
            self._heartbeat = claim.heartbeat_seconds
        jobs = [job for job in claim.jobs if job.stage in self._handlers]
        for job in claim.jobs:
            if job.stage not in self._handlers:  # not in our capabilities: give it back at once
                await self._release(job, count=False)
        if jobs:
            self._emit({"event": "claimed", "count": len(jobs), "stage": jobs[0].stage.value})
        return jobs

    async def _wait(self, active: set[asyncio.Task[None]]) -> None:
        waiter = asyncio.create_task(self._stop.wait())
        pending: set[asyncio.Future[Any]] = {waiter, *active}
        try:
            _, _ = await asyncio.wait(
                pending, timeout=self._options.poll_interval_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )  # fmt: skip
        finally:
            waiter.cancel()

    async def _shutdown(self, active: set[asyncio.Task[None]]) -> None:
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)

    async def _process(self, job: Job) -> None:
        handler = self._handlers[job.stage]
        started = self._clock()
        began = time.monotonic()
        self._emit({"event": "job_started", "job_id": str(job.job_id), "stage": job.stage.value,
                    "asset_id": str(job.asset_id), "attempt": job.attempt})  # fmt: skip
        work = asyncio.ensure_future(self._run_handler(handler, job))
        beat = asyncio.create_task(self._heartbeats(job, work))
        try:
            body = await work
        except asyncio.CancelledError:
            work.cancel()
            beat.cancel()
            if job.job_id not in self._lease_lost:
                await asyncio.shield(self._release(job))
            raise
        finally:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
        body.setdefault("started_at", started.isoformat())
        body.setdefault("duration_ms", round((time.monotonic() - began) * 1000))
        await self._submit(job, body)

    async def _run_handler(self, handler: StageHandler, job: Job) -> dict[str, Any]:
        started, began = self._clock(), time.monotonic()
        try:
            output: BaseModel = await handler.run(job, self._ctx)
            return {
                "ok": True,
                "output": output.model_dump(mode="json", by_alias=True),
                "model_id": f"rushbase-worker/{job.stage.value}",
                "model_version": get_version(),
            }
        except StageError as exc:
            return failure_body(str(exc), exc.retryable, started, began)
        except ValidationError as exc:
            return failure_body(f"output violates the stage contract: {exc}", False, started, began)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("job %s (%s) crashed", job.job_id, job.stage)
            return failure_body(f"{type(exc).__name__}: {exc}", True, started, began)

    async def _heartbeats(self, job: Job, work: "asyncio.Future[dict[str, Any]]") -> None:
        while True:
            await self._sleep(self._heartbeat)
            try:
                await self._api.heartbeat(job.job_id)
            except LeaseLostError:
                self._lease_lost.add(job.job_id)
                self.summary.lease_lost += 1
                self._emit({"event": "lease_lost", "job_id": str(job.job_id)})
                work.cancel()
                return
            except (UnavailableError, ApiError) as exc:
                log.warning("heartbeat for %s failed: %s", job.job_id, exc)

    async def _submit(self, job: Job, body: dict[str, Any]) -> None:
        try:
            ack = await self._api.result(job.job_id, body)
        except LeaseLostError:
            self.summary.lease_lost += 1
            self._emit({"event": "lease_lost", "job_id": str(job.job_id)})
            return
        except (UnavailableError, ApiError) as exc:
            log.error("could not report job %s: %s", job.job_id, exc)
            self._emit({"event": "report_failed", "job_id": str(job.job_id), "error": str(exc)})
            return
        stage = job.stage.value
        if body["ok"]:
            self.summary.completed += 1
            self.summary.by_stage[stage] = self.summary.by_stage.get(stage, 0) + 1
            self._emit(
                {
                    "event": "job_done",
                    "job_id": str(job.job_id),
                    "stage": stage,
                    "duration_ms": body["duration_ms"],
                    "job_state": ack.job_state,
                }
            )
        else:
            self.summary.failed += 1
            self._emit(
                {
                    "event": "job_failed",
                    "job_id": str(job.job_id),
                    "stage": stage,
                    "error": body["error"],
                    "retryable": body["retryable"],
                }
            )

    async def _release(self, job: Job, *, count: bool = True) -> None:
        try:
            await self._api.release(job.job_id)
        except (LeaseLostError, UnavailableError, ApiError) as exc:
            log.warning("release of %s failed: %s", job.job_id, exc)
            return
        if count:
            self.summary.released += 1
        self._emit({"event": "job_released", "job_id": str(job.job_id), "stage": job.stage.value})
