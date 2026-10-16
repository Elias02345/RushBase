"""``classify`` stage (CPU): taxonomy mapping, text embeddings, proposals and review reasons."""

import asyncio
from collections.abc import Callable, Sequence
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ValidationError

from rushbase_core import Stage
from rushbase_core.results_ai import (
    AudioOutput,
    ClassifyOutput,
    EmbedOutput,
    TagAssignment,
    TextEmbedding,
    VisionOutput,
)
from rushbase_worker.assets.plan import IMAGE_EMBEDDING_MODEL, TEXT_EMBEDDING_MODEL
from rushbase_worker.classify.build import (
    audio_tags,
    chunk_transcript,
    dedupe_tags,
    description_terms,
    disagreement_reasons,
    guess_tags,
    map_terms,
    middle_vectors,
    proposals_for,
    vision_descriptions,
)
from rushbase_worker.classify.matcher import TaxonomyMatcher
from rushbase_worker.classify.review import low_confidence_reasons
from rushbase_worker.classify.zeroshot import ZeroShot
from rushbase_worker.embedding.siglip import to_vector
from rushbase_worker.models import Job
from rushbase_worker.runtimes.manager import Runtimes
from rushbase_worker.stages.ai_base import AiStage
from rushbase_worker.stages.base import Requirements, StageContext, StageError
from rushbase_worker.taxonomy import EMPTY_SNAPSHOT

Language = Literal["de", "en"]


def upstream_model[M: BaseModel](job: Job, name: str, model: type[M]) -> M | None:
    """A validated upstream result, or ``None`` when that stage did not run."""
    raw = job.upstream.get(name)
    if raw is None:
        return None
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise StageError(f"upstream '{name}' result is invalid: {exc}", retryable=False) from exc


class ClassifyStage(AiStage):
    """Runs on any CPU: multilingual-e5 text vectors plus SigLIP text-tower zero-shot checks."""

    stage = Stage.CLASSIFY
    requirements = Requirements(ai=True)

    async def process(self, job: Job, ctx: StageContext, runtimes: Runtimes) -> ClassifyOutput:
        await runtimes.ensure_models(TEXT_EMBEDDING_MODEL, IMAGE_EMBEDDING_MODEL)
        taxonomy = await ctx.taxonomy.get() if ctx.taxonomy else EMPTY_SNAPSHOT
        vision = upstream_model(job, "vision", VisionOutput)
        audio = upstream_model(job, "audio", AudioOutput)
        embed = upstream_model(job, "embed", EmbedOutput)
        settings = ctx.settings

        def work() -> ClassifyOutput:
            e5 = runtimes.e5_embedder()
            matcher = TaxonomyMatcher(
                taxonomy, lambda texts: e5.embed(texts, prefix="query: "),
                settings.taxonomy_match_threshold,
            )  # fmt: skip
            descriptions = vision_descriptions(vision)

            tags: list[TagAssignment] = guess_tags(descriptions)
            unmapped = []
            for description in descriptions:
                mapped, free = map_terms(description_terms(description), matcher, "vision")
                tags += mapped
                unmapped += free
            if audio is not None:
                tags += audio_tags(audio, matcher)
            single = {f.key for f in taxonomy.fields if f.kind == "single"}

            def threshold(name: str) -> float:
                return settings.review_field_confidence.get(
                    name, settings.review_default_confidence
                )

            reasons = low_confidence_reasons(descriptions, threshold)
            vectors = middle_vectors(embed)
            if vectors and descriptions:
                siglip_text = runtimes.siglip_text_embedder()
                reasons += disagreement_reasons(
                    descriptions, vectors, ZeroShot(siglip_text.embed), settings
                )
            return ClassifyOutput(
                text_model=runtimes.model_ref(TEXT_EMBEDDING_MODEL),
                taxonomy_version=taxonomy.version,
                tags=dedupe_tags(tags, single),
                proposals=proposals_for(
                    unmapped, matcher, len(descriptions), settings.proposal_min_confidence
                ),
                text_embeddings=text_embeddings(
                    e5.embed, vision, audio, settings.transcript_chunk_seconds
                ),
                review_reasons=list(dict.fromkeys(reasons)),
            )

        return await asyncio.to_thread(work)


def text_embeddings(
    embed_passages: Callable[[Sequence[str]], NDArray[np.float32]],
    vision: VisionOutput | None,
    audio: AudioOutput | None,
    chunk_seconds: float,
) -> list[TextEmbedding]:
    """e5 passage vectors for the asset summary, shot descriptions and transcript chunks."""
    items: list[tuple[str, dict[str, object]]] = []
    if vision is not None:
        if vision.summary is not None:
            summary = vision.summary
            items.append(
                (
                    f"{summary.short_de} {summary.long_de}",
                    {"kind": "asset_summary", "language": "de"},
                )
            )
            items.append(
                (
                    f"{summary.short_en} {summary.long_en}",
                    {"kind": "asset_summary", "language": "en"},
                )
            )
        for d in vision.descriptions:
            items.append(
                (
                    f"{d.short_de} {d.long_de}",
                    {"kind": "shot_description", "shot_idx": d.shot_idx, "language": "de"},
                )
            )
            items.append(
                (
                    f"{d.short_en} {d.long_en}",
                    {"kind": "shot_description", "shot_idx": d.shot_idx, "language": "en"},
                )
            )
    if audio is not None and audio.transcript is not None:
        transcript = audio.transcript
        language = transcript.language if transcript.language in {"de", "en"} else None
        for start, end, text in chunk_transcript(transcript, round(chunk_seconds * 1000)):
            items.append(
                (
                    text,
                    {
                        "kind": "transcript_chunk",
                        "start_ms": start,
                        "end_ms": end,
                        "language": language,
                    },
                )
            )
    if not items:
        return []
    matrix: NDArray[np.float32] = embed_passages([text for text, _ in items])
    return [
        TextEmbedding(vector=to_vector(row), **meta)
        for (_, meta), row in zip(items, matrix, strict=True)
    ]
