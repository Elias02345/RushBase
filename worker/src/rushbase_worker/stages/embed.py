"""``embed`` stage: SigLIP 2 vectors of keyframes (video) and the preview (photo)."""

import asyncio
from pathlib import Path

from rushbase_core import Stage
from rushbase_core.results_ai import EmbedOutput, ImageEmbedding
from rushbase_worker.assets.plan import IMAGE_EMBEDDING_MODEL
from rushbase_worker.embedding.siglip import to_vector
from rushbase_worker.models import Job
from rushbase_worker.runtimes.manager import Runtimes
from rushbase_worker.stages.ai_base import AiStage
from rushbase_worker.stages.base import Requirements, StageContext, StageError
from rushbase_worker.stages.inputs import photo_path, shots_of


class EmbedStage(AiStage):
    """Image embeddings in the shared text/image space (cross-modal search, ADR-0007)."""

    stage = Stage.EMBED
    requirements = Requirements(ai=True)

    async def process(self, job: Job, ctx: StageContext, runtimes: Runtimes) -> EmbedOutput:
        targets: list[tuple[int | None, str, Path]] = []
        if job.media_type == "photo":
            targets.append((None, "photo", photo_path(job, ctx)))
        elif job.media_type == "video":
            for shot in shots_of(job).shots:
                targets += [
                    (shot.idx, kf.position, ctx.derived_path(kf.rel_path)) for kf in shot.keyframes
                ]
        else:
            raise StageError(f"embed does not apply to {job.media_type}", retryable=False)
        missing = [p for _, _, p in targets if not p.is_file()]
        if missing:
            raise StageError(f"keyframe files are missing: {missing[0].name}")
        await runtimes.ensure_models(IMAGE_EMBEDDING_MODEL)
        embedder = runtimes.image_embedder()
        vectors = await asyncio.to_thread(embedder.embed_files, [p for _, _, p in targets])
        images = [
            ImageEmbedding(shot_idx=idx, keyframe=position, vector=to_vector(vector))
            for (idx, position, _), vector in zip(targets, vectors, strict=True)
        ]
        return EmbedOutput(
            model=runtimes.model_ref(IMAGE_EMBEDDING_MODEL, runtimes.vision_embed_role),
            images=images,
        )
