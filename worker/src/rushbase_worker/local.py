"""Run stages on a single local file without a server (``rushbase analyze``)."""

import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rushbase_core import MediaType, Stage, media_type_for
from rushbase_worker.config import WorkerSettings
from rushbase_worker.models import Job, JobFile
from rushbase_worker.stages import HANDLERS, StageContext, StageError, missing_tools
from rushbase_worker.tools.toolbox import Toolbox

ANALYZE_STAGES = (Stage.PROBE, Stage.PREVIEW, Stage.SHOTS)


async def analyze_file(
    settings: WorkerSettings,
    path: Path,
    output_dir: Path,
    stages: tuple[Stage, ...] = ANALYZE_STAGES,
    *,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Run ``stages`` in pipeline order on ``path``; derived files go below ``output_dir``.

    Returns ``{"file", "media_type", "asset_id", "output_dir", "stages": {name: {...}}}`` where
    each stage entry has ``ok``, ``duration_ms`` and ``output`` or ``error``.
    """
    media = media_type_for(path.name)
    toolbox = Toolbox(settings)
    ctx = StageContext(settings, toolbox, path.parent.resolve(), output_root=output_dir.resolve())
    job_files = [JobFile(role="primary", rel_path=path.name, size=path.stat().st_size)]
    asset_id = uuid.uuid4()
    upstream: dict[str, dict[str, Any]] = {}
    report: dict[str, Any] = {}
    for stage in (s for s in Stage if s in stages and s in HANDLERS):
        handler = HANDLERS[stage]
        began = time.monotonic()
        entry: dict[str, Any]
        absent = missing_tools(handler, toolbox)
        if absent:
            entry = {"ok": False, "error": f"missing tools: {', '.join(absent)}"}
        elif stage is Stage.SHOTS and media is not MediaType.VIDEO:
            entry = {"ok": True, "skipped": f"not applicable to {media.value}"}
        else:
            job = Job(
                job_id=uuid.uuid4(),
                asset_id=asset_id,
                stage=stage,
                media_type=media.value,
                lease_expires_at=datetime.now(UTC),
                files=job_files,
                upstream=upstream,
            )
            try:
                output = await handler.run(job, ctx)
            except StageError as exc:
                entry = {"ok": False, "error": str(exc)}
            else:
                document = output.model_dump(mode="json", by_alias=True)
                upstream[stage.value] = document
                shown = {k: v for k, v in document.items() if include_raw or k != "raw"}
                entry = {"ok": True, "output": shown}
        entry["duration_ms"] = round((time.monotonic() - began) * 1000)
        report[stage.value] = entry
    return {
        "file": str(path),
        "media_type": media.value,
        "asset_id": str(asset_id),
        "output_dir": str(output_dir.resolve()),
        "stages": report,
    }
