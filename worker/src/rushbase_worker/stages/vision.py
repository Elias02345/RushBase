"""``vision`` stage: structured shot/photo descriptions from a local vision-language model."""

from rushbase_core import Stage
from rushbase_core.results_ai import AssetSummary, ModelRef, VisionOutput, VisualDescription
from rushbase_worker.config import WorkerSettings
from rushbase_worker.models import Job
from rushbase_worker.runtimes.llama import LlamaServer
from rushbase_worker.runtimes.manager import Runtimes
from rushbase_worker.runtimes.profiles import Variant
from rushbase_worker.stages.ai_base import AiStage
from rushbase_worker.stages.base import Requirements, StageContext, StageError
from rushbase_worker.stages.inputs import photo_path, shots_of
from rushbase_worker.taxonomy import EMPTY_SNAPSHOT, TaxonomySnapshot
from rushbase_worker.vision.describe import (
    PROMPT_VERSION,
    Frame,
    VisionError,
    describe_photo,
    describe_shot,
    summarize,
)

PROFILE_NAMES = {"xl", "gpu8", "gpu4", "cpu"}


def variant_profile(variant: Variant, default: str) -> str:
    """Profile a variant belongs to (``gpu8-4b`` -> ``gpu8``; baselines keep the detected one)."""
    prefix = variant.name.split("-", 1)[0]
    return prefix if prefix in PROFILE_NAMES else default


def summary_from_description(description: VisualDescription) -> AssetSummary:
    """A one-shot clip or a photo needs no extra call: its description is the summary."""
    return AssetSummary(
        short_de=description.short_de,
        short_en=description.short_en,
        long_de=description.long_de,
        long_en=description.long_en,
    )


class VisionStage(AiStage):
    """Describes each shot (up to three keyframes in one request) or the photo."""

    stage = Stage.VISION
    requirements = Requirements(ai=True, gpu=True)

    async def process(self, job: Job, ctx: StageContext, runtimes: Runtimes) -> VisionOutput:
        taxonomy = await ctx.taxonomy.get() if ctx.taxonomy else EMPTY_SNAPSHOT
        settings: WorkerSettings = ctx.settings
        async with runtimes.vlm() as server:
            max_tokens = settings.vision_max_tokens
            try:
                if job.media_type == "photo":
                    description, _ = await describe_photo(
                        server, Frame(photo_path(job, ctx), label="photo"), taxonomy,
                        max_tokens=max_tokens,
                    )  # fmt: skip
                    descriptions = [description]
                    summary: AssetSummary | None = summary_from_description(description)
                elif job.media_type == "video":
                    descriptions, summary = await self._video(
                        job, ctx, server, taxonomy, max_tokens
                    )
                else:
                    raise StageError(f"vision does not apply to {job.media_type}", retryable=False)
            except VisionError as exc:
                raise StageError(f"vision model gave no valid answer: {exc}") from exc
            variant = server.variant
        spec = runtimes.spec(variant.model)
        model = ModelRef(
            model_id=variant.model,
            revision=spec.file("model").sha256[:12],
            profile=variant_profile(variant, runtimes.profile.value),
            prompt_version=PROMPT_VERSION,
        )
        return VisionOutput(model=model, descriptions=descriptions, summary=summary)

    @staticmethod
    async def _video(
        job: Job,
        ctx: StageContext,
        server: LlamaServer,
        taxonomy: TaxonomySnapshot,
        max_tokens: int,
    ) -> tuple[list[VisualDescription], AssetSummary | None]:
        shots = shots_of(job).shots
        if not shots:
            raise StageError("the shots result has no shots", retryable=False)
        descriptions: list[VisualDescription] = []
        for shot in shots:
            frames = [
                Frame(ctx.derived_path(k.rel_path), k.t_ms, k.position) for k in shot.keyframes
            ]
            if not frames or not all(f.path.is_file() for f in frames):
                raise StageError(f"keyframes of shot {shot.idx} are missing")
            description, _ = await describe_shot(
                server, frames, shot.end_ms - shot.start_ms, shot.idx, taxonomy,
                max_tokens=max_tokens,
            )  # fmt: skip
            descriptions.append(description)
        if len(descriptions) == 1:
            return descriptions, summary_from_description(descriptions[0])
        summary, _ = await summarize(server, descriptions, shots[-1].end_ms, max_tokens=max_tokens)
        return descriptions, summary
