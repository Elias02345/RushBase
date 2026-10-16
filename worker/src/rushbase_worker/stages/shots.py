"""``shots`` stage: shot boundaries, keyframes, camera motion and quality per shot."""

import asyncio
import contextlib
import os
from pathlib import Path

from pydantic import BaseModel

from rushbase_core import Stage
from rushbase_core.results import CameraMotion, Estimated, Keyframe, ShotOut, ShotsOutput, Source
from rushbase_worker.models import Job
from rushbase_worker.preview.derived import temp_name
from rushbase_worker.preview.video import extract_frame
from rushbase_worker.shots.analyze import FrameAnalyzer, ShotMetrics
from rushbase_worker.shots.detect import DETECTOR_NAME
from rushbase_worker.shots.frames import analysis_size, display_size, stream_frames
from rushbase_worker.stages.base import Requirements, StageContext, StageError
from rushbase_worker.stages.preview import choose_video_source, select_lut
from rushbase_worker.tools.toolbox import Toolbox

KEYFRAME_DIR = "keyframes"
KEYFRAME_PARALLEL = 2
MIN_INSET_MS = 40
MAX_INSET_MS = 250
DEFAULT_FPS = 25.0


def keyframe_times(start_ms: int, end_ms: int) -> list[tuple[str, int]]:
    """Start/middle/end keyframe times, slightly inside the shot boundaries."""
    length = max(0, end_ms - start_ms)
    inset = min(max(MIN_INSET_MS, length // 10), MAX_INSET_MS, length // 4)
    return [
        ("start", start_ms + inset),
        ("middle", start_ms + length // 2),
        ("end", max(start_ms, end_ms - inset - 1)),
    ]


def shot_bounds_ms(metrics: list[ShotMetrics], fps: float, total_ms: int) -> list[tuple[int, int]]:
    """Millisecond ``(start, end)`` per shot; shots tile the clip, the last ends at ``total_ms``."""
    starts = [round(m.start_frame / fps * 1000) for m in metrics]
    ends = [*starts[1:], max(total_ms, starts[-1])] if metrics else []
    return list(zip(starts, ends, strict=True))


def build_shot(index: int, metrics: ShotMetrics, start_ms: int, end_ms: int) -> ShotOut:
    """Contract model for one shot (keyframes are added by the caller)."""
    motion: Estimated[CameraMotion] | None = (
        Estimated(
            value=metrics.camera_motion, source=Source.AI, confidence=metrics.motion_confidence
        )
        if metrics.camera_motion is not None
        else None
    )
    return ShotOut(
        idx=index,
        start_ms=start_ms,
        end_ms=end_ms,
        keyframes=[],
        camera_motion=motion,
        sharpness=metrics.sharpness,
        shake=metrics.shake,
        exposure=metrics.exposure,
        noise=metrics.noise,
    )


class ShotsStage:
    """Detect shots on a reduced-resolution decode and measure them."""

    stage = Stage.SHOTS
    requirements = Requirements(tools=("ffmpeg",))

    async def run(self, job: Job, ctx: StageContext) -> BaseModel:
        """Analyse the video of ``job``'s asset."""
        probe = job.upstream.get("probe", {})
        source_rel = choose_video_source(job, probe)
        if source_rel is None:
            return ShotsOutput(detector=DETECTOR_NAME, shots=[])
        original = ctx.source(source_rel)
        if not original.is_file():
            raise StageError(f"file not found: {source_rel}")
        video = probe.get("video") or {}
        width, height = video.get("width"), video.get("height")
        if not width or not height:
            raise StageError("probe result lacks the video size", retryable=False)
        fps = min(float(video.get("fps") or DEFAULT_FPS), ctx.settings.analysis_max_fps)
        size = analysis_size(
            *display_size(width, height, video.get("rotation")), ctx.settings.analysis_width
        )
        decode_from = self._analysis_source(job, ctx) or original

        analyzer = FrameAnalyzer(fps, ctx.settings.min_shot_seconds)
        async with asyncio.timeout(ctx.settings.long_tool_timeout_seconds):
            async for frame in stream_frames(ctx.toolbox, decode_from, size, fps, hwaccel=True):
                await asyncio.to_thread(analyzer.feed, frame)
        if analyzer.frames == 0:
            raise StageError("no video frames could be decoded")
        metrics = await asyncio.to_thread(analyzer.finish)

        total_ms = int(probe.get("duration_ms") or round(analyzer.frames / fps * 1000))
        bounds = shot_bounds_ms(metrics, fps, total_ms)
        shots = [
            build_shot(i, m, s, e)
            for i, (m, (s, e)) in enumerate(zip(metrics, bounds, strict=True))
        ]
        lut = select_lut(ctx.settings.luts, (probe.get("exposure") or {}).get("log_profile"))
        directory = ctx.derived_dir(job.asset_id) / KEYFRAME_DIR
        keyframes = await self._keyframes(ctx.toolbox, ctx, original, bounds, directory, lut)
        return ShotsOutput(
            detector=DETECTOR_NAME,
            shots=[s.model_copy(update={"keyframes": keyframes[s.idx]}) for s in shots],
        )

    @staticmethod
    def _analysis_source(job: Job, ctx: StageContext) -> Path | None:
        """The 720p proxy from the preview stage when available (much cheaper to decode)."""
        for entry in job.upstream.get("preview", {}).get("files", []):
            if entry.get("kind") == "proxy":
                path = ctx.derived_path(entry["rel_path"])
                if path.is_file():
                    return path
        return None

    async def _keyframes(
        self,
        toolbox: Toolbox,
        ctx: StageContext,
        source: Path,
        bounds: list[tuple[int, int]],
        directory: Path,
        lut: Path | None,
    ) -> list[list[Keyframe]]:
        directory.mkdir(parents=True, exist_ok=True)
        for stale in directory.glob(".*.rbtmp*"):
            stale.unlink(missing_ok=True)
        wanted: set[str] = set()
        gate = asyncio.Semaphore(KEYFRAME_PARALLEL)
        results: list[list[Keyframe]] = [[] for _ in bounds]

        async def one(shot: int, position: str, t_ms: int) -> None:
            final = directory / f"shot-{shot:04d}-{position}.jpg"
            wanted.add(final.name)
            tmp = temp_name(final)
            async with gate:
                try:
                    await extract_frame(
                        toolbox,
                        source,
                        t_ms / 1000,
                        tmp,
                        ctx.settings.keyframe_width,
                        lut,
                        quality=3,
                    )
                    os.replace(tmp, final)
                except BaseException:
                    tmp.unlink(missing_ok=True)
                    raise
            results[shot].append(
                Keyframe.model_validate(
                    {"t_ms": t_ms, "rel_path": ctx.rel(final), "position": position}
                )
            )

        try:
            async with asyncio.TaskGroup() as group:
                for shot, (start, end) in enumerate(bounds):
                    for position, t_ms in keyframe_times(start, end):
                        group.create_task(one(shot, position, t_ms))
        except ExceptionGroup as group_error:
            raise group_error.exceptions[0] from None
        for leftover in directory.glob("shot-*.jpg"):
            if leftover.name not in wanted:
                with contextlib.suppress(OSError):
                    leftover.unlink()
        order = {"start": 0, "middle": 1, "end": 2}
        return [sorted(frames, key=lambda k: order[k.position]) for frames in results]
