"""What the AI stages read from the results of earlier stages (``job.upstream``)."""

from pathlib import Path

from pydantic import ValidationError

from rushbase_core.results import PreviewOutput, ShotsOutput
from rushbase_worker.models import Job
from rushbase_worker.stages.base import StageContext, StageError


def shots_of(job: Job) -> ShotsOutput:
    """The ``shots`` result of a video job."""
    raw = job.upstream.get("shots")
    if raw is None:
        raise StageError("upstream result 'shots' is missing for this video")
    try:
        return ShotsOutput.model_validate(raw)
    except ValidationError as exc:
        raise StageError(f"upstream 'shots' result is invalid: {exc}", retryable=False) from exc


def photo_path(job: Job, ctx: StageContext) -> Path:
    """The image the AI stages look at for a photo: the 2048 px preview, else the file itself."""
    raw = job.upstream.get("preview")
    if raw is not None:
        try:
            preview = PreviewOutput.model_validate(raw)
        except ValidationError:
            preview = None
        if preview is not None:
            for file in preview.files:
                if file.kind == "photo_preview":
                    candidate = ctx.derived_path(file.rel_path)
                    if candidate.is_file():
                        return candidate
    return ctx.source(job.primary().rel_path)
