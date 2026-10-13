"""``probe`` stage: ffprobe + ExifTool + camera sidecar XML into :class:`ProbeOutput`."""

import asyncio
import logging
from typing import Any

from pydantic import BaseModel

from rushbase_core import MediaType, Stage
from rushbase_worker.models import Job
from rushbase_worker.probe.decode import match_camera_proxy
from rushbase_worker.probe.normalize import build_probe_output
from rushbase_worker.probe.sidecar import read_sidecar, sidecar_candidates
from rushbase_worker.stages.base import Requirements, StageContext, StageError
from rushbase_worker.tools.process import ToolError

log = logging.getLogger(__name__)


class ProbeStage:
    """Extract technical metadata (the normalised fields plus the complete raw tool output)."""

    stage = Stage.PROBE
    requirements = Requirements(tools=("ffprobe", "exiftool"))

    async def run(self, job: Job, ctx: StageContext) -> BaseModel:
        """Probe the primary file of ``job``."""
        primary = job.primary()
        path = ctx.source(primary.rel_path)
        if not path.is_file():
            raise StageError(f"file not found: {primary.rel_path}")
        ffprobe_result, exif_result = await asyncio.gather(
            ctx.toolbox.ffprobe(path), ctx.toolbox.exiftool(path), return_exceptions=True
        )
        ffprobe_data = self._ok("ffprobe", ffprobe_result)
        exif_data = self._ok("exiftool", exif_result)
        if ffprobe_data is None and exif_data is None:
            raise StageError(f"neither ffprobe nor exiftool could read {primary.rel_path}")

        sidecars: dict[str, dict[str, Any]] = {}
        pairs = [(f.role, f.rel_path) for f in job.files]
        for rel_path in sidecar_candidates(pairs):
            try:
                sidecars[rel_path] = await asyncio.to_thread(read_sidecar, ctx.source(rel_path))
            except (OSError, ValueError) as exc:
                log.warning("ignoring sidecar %s: %s", rel_path, exc)

        proxies = [f.rel_path for f in job.files_with_role("proxy_camera")]
        return build_probe_output(
            media_type=MediaType(job.media_type),
            primary_rel_path=primary.rel_path,
            ffprobe=ffprobe_data,
            exiftool=exif_data,
            sidecars=sidecars,
            decoders=await ctx.toolbox.decoders(),
            camera_proxy=match_camera_proxy(primary.rel_path, proxies),
        )

    @staticmethod
    def _ok(tool: str, result: dict[str, Any] | BaseException) -> dict[str, Any] | None:
        if isinstance(result, ToolError):
            log.warning("%s failed: %s", tool, result)
            return None
        if isinstance(result, BaseException):
            raise result
        return result
