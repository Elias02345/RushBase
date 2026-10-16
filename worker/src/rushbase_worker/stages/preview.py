"""``preview`` stage: poster, sprite, proxy, waveform and photo previews into ``derived/``."""

import asyncio
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from rushbase_core import MediaType, Stage
from rushbase_core.results import DerivedFile, PreviewOutput
from rushbase_worker.models import Job
from rushbase_worker.preview import audio as audio_preview
from rushbase_worker.preview import video as video_preview
from rushbase_worker.preview.derived import atomic_output, describe
from rushbase_worker.preview.phash import dhash
from rushbase_worker.preview.photo import PhotoDecodeError, load_photo, resized
from rushbase_worker.probe.decode import match_camera_proxy
from rushbase_worker.stages.base import Requirements, StageContext, StageError

PHOTO_QUALITY = 85
POSTER_QUALITY = 82


def profile_key(name: str) -> str:
    """Normalise a log profile name for LUT lookup (``S-Log3`` and ``slog3`` are the same)."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def select_lut(luts: dict[str, Path], log_profile: str | None) -> Path | None:
    """The configured ``.cube`` LUT for ``log_profile`` or ``None`` (no transform)."""
    if not log_profile:
        return None
    by_key = {profile_key(name): path for name, path in luts.items()}
    return by_key.get(profile_key(log_profile))


def choose_video_source(job: Job, probe: dict[str, Any]) -> str | None:
    """Library-relative file to decode for previews, or ``None`` when nothing is decodable.

    The original, unless probe found it undecodable and a matching in-camera proxy exists.
    """
    primary = job.primary().rel_path
    status = probe.get("decode_status", "ok")
    if status == "ok":
        return primary
    if status == "proxy_only":
        proxies = [f.rel_path for f in job.files_with_role("proxy_camera")]
        return match_camera_proxy(primary, proxies)
    return None


class PreviewStage:
    """Derived previews per media type. Writes only below ``.rushbase/derived/<asset-id>/``."""

    stage = Stage.PREVIEW
    requirements = Requirements(tools=("ffmpeg",))

    async def run(self, job: Job, ctx: StageContext) -> BaseModel:
        """Create the previews of ``job``'s asset."""
        media = MediaType(job.media_type)
        directory = ctx.derived_dir(job.asset_id)
        for stale in directory.glob(".*.rbtmp*"):
            stale.unlink(missing_ok=True)
        probe = job.upstream.get("probe", {})
        if media is MediaType.PHOTO:
            return await self._photo(job, ctx, directory)
        if media is MediaType.AUDIO:
            return await self._audio(job, ctx, directory, probe)
        if media is MediaType.VIDEO:
            return await self._video(job, ctx, directory, probe)
        return PreviewOutput(files=[])

    async def _video(
        self, job: Job, ctx: StageContext, directory: Path, probe: dict[str, Any]
    ) -> PreviewOutput:
        source_rel = choose_video_source(job, probe)
        if source_rel is None:
            return PreviewOutput(files=[])  # metadata only; the UI shows the decode status
        source = ctx.source(source_rel)
        if not source.is_file():
            raise StageError(f"file not found: {source_rel}")
        settings, tools = ctx.settings, ctx.toolbox
        duration = probe.get("duration_ms")
        log_profile = (probe.get("exposure") or {}).get("log_profile")
        lut = select_lut(settings.luts, log_profile)
        if lut is not None and not lut.is_file():
            raise StageError(f"configured LUT not found: {lut}", retryable=False)

        files: list[DerivedFile] = []
        poster = directory / "poster.webp"
        image = await video_preview.make_poster(
            tools, settings, source, video_preview.poster_time(duration), poster, lut
        )
        files.append(describe("poster", poster, ctx.rel(poster), *image.size))
        sprite = directory / f"sprite-{settings.sprite_tiles}x1-{settings.sprite_tile_width}.jpg"
        size = await video_preview.make_sprite(tools, settings, source, duration, sprite, lut)
        files.append(describe("thumbnail", sprite, ctx.rel(sprite), *size))
        if settings.proxy_enabled:
            proxy = directory / "proxy.mp4"
            await video_preview.make_proxy(tools, settings, source, proxy, lut)
            files.append(describe("proxy", proxy, ctx.rel(proxy)))
        if probe.get("audio") is not None:
            files += await self._waveform(ctx, directory, source, duration)
        return PreviewOutput(
            files=files,
            normalization=f"lut3d:{lut.name}" if lut else None,
            perceptual_hash=await asyncio.to_thread(dhash, image),
        )

    async def _audio(
        self, job: Job, ctx: StageContext, directory: Path, probe: dict[str, Any]
    ) -> PreviewOutput:
        source = ctx.source(job.primary().rel_path)
        if not source.is_file():
            raise StageError(f"file not found: {job.primary().rel_path}")
        files = await self._waveform(ctx, directory, source, probe.get("duration_ms"))
        return PreviewOutput(files=files)

    async def _waveform(
        self, ctx: StageContext, directory: Path, source: Path, duration_ms: int | None
    ) -> list[DerivedFile]:
        png, peaks = directory / "waveform.png", directory / "waveform.json"
        await audio_preview.make_waveform(
            ctx.toolbox, ctx.settings, source, duration_ms, png, peaks
        )
        return [
            describe(
                "waveform",
                png,
                ctx.rel(png),
                ctx.settings.waveform_width,
                ctx.settings.waveform_height,
            ),
            describe("other", peaks, ctx.rel(peaks)),
        ]

    async def _photo(self, job: Job, ctx: StageContext, directory: Path) -> PreviewOutput:
        chosen = (job.files_with_role("jpeg") or [job.primary()])[0]
        source = ctx.source(chosen.rel_path)
        if not source.is_file():
            raise StageError(f"file not found: {chosen.rel_path}")
        settings = ctx.settings
        try:
            full = await asyncio.to_thread(load_photo, source)
        except PhotoDecodeError as exc:
            raise StageError(str(exc), retryable=False) from exc

        def write() -> tuple[list[DerivedFile], str]:
            written: list[DerivedFile] = []
            poster_image = resized(full, settings.poster_width)
            preview_image = resized(full, settings.photo_preview_width)
            for kind, name, image, quality in (
                ("poster", "poster.webp", poster_image, POSTER_QUALITY),
                ("photo_preview", "preview.webp", preview_image, PHOTO_QUALITY),
            ):
                final = directory / name
                with atomic_output(final) as out:
                    image.save(out, "WEBP", quality=quality, method=4)
                written.append(describe(kind, final, ctx.rel(final), *image.size))
            return written, dhash(poster_image)

        files, phash = await asyncio.to_thread(write)
        return PreviewOutput(files=files, perceptual_hash=phash)
