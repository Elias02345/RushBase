"""Assembling the ``classify`` result from the vision, audio and embed results."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from rushbase_core.results_ai import (
    AudioOutput,
    EmbedOutput,
    TagAssignment,
    TermProposal,
    Transcript,
    VisionOutput,
    VisualDescription,
)
from rushbase_worker.audio.events import tag_display_name
from rushbase_worker.classify.matcher import TaxonomyMatcher, normalize
from rushbase_worker.classify.zeroshot import ZeroShot
from rushbase_worker.config import WorkerSettings
from rushbase_worker.embedding.siglip import from_vector

Origin = Literal["vision", "audio", "metadata"]
MAX_PROPOSALS = 10
PROPOSAL_NEAR_FIELD_SIMILARITY = 0.6
STRUCTURED_TERMS = ("interior_exterior", "time_of_day", "shot_size", "camera_angle")
LABEL_LISTS = ("objects", "mood", "suitability")


@dataclass(frozen=True)
class Term:
    """A phrase from the analysis that may map to an approved value."""

    text: str
    confidence: float
    shot_idx: int | None
    free: bool
    """Free text (objects, mood, ...) may be mapped by similarity and may become a proposal."""


def description_terms(description: VisualDescription) -> list[Term]:
    """Candidate terms of one description (structured enums and free labels)."""
    shot = description.shot_idx
    terms: list[Term] = []
    for name in STRUCTURED_TERMS:
        estimate = getattr(description, name)
        if estimate is not None and str(estimate.value) != "unknown":
            terms.append(Term(str(estimate.value), estimate.confidence or 0.0, shot, False))
    for name in ("subject", "environment", "action"):
        estimate = getattr(description, name)
        if estimate is not None:
            terms.append(Term(estimate.value, estimate.confidence or 0.0, shot, True))
    for name in LABEL_LISTS:
        terms += [
            Term(item.label, item.confidence, shot, True) for item in getattr(description, name)
        ]
    return terms


def map_terms(
    terms: Sequence[Term], matcher: TaxonomyMatcher, origin: Origin
) -> tuple[list[TagAssignment], list[Term]]:
    """Tag assignments for the terms that map, and the free terms that do not."""
    tags: list[TagAssignment] = []
    unmapped: list[Term] = []
    for term in terms:
        match = matcher.match(term.text, use_embedding=term.free)
        if match is None:
            if term.free:
                unmapped.append(term)
            continue
        tags.append(
            TagAssignment(
                field=match.field,
                value=match.value,
                confidence=round(min(1.0, term.confidence * match.confidence), 4),
                shot_idx=term.shot_idx,
                method=match.method,
                origin=origin,
            )
        )
    return tags, unmapped


def guess_tags(descriptions: list[VisualDescription]) -> list[TagAssignment]:
    """The VLM's own taxonomy choices (enum-constrained, hence exact)."""
    return [
        TagAssignment(
            field=g.field,
            value=g.value,
            confidence=g.confidence,
            shot_idx=d.shot_idx,
            method="exact",
            origin="vision",
        )
        for d in descriptions
        for g in d.taxonomy
    ]


def audio_tags(audio: AudioOutput, matcher: TaxonomyMatcher) -> list[TagAssignment]:
    """Audio event tags that exist as approved values (by tag name or its display names)."""
    tags: list[TagAssignment] = []
    for event in audio.events:
        for name in (
            event.tag,
            tag_display_name(event.tag, "en"),
            tag_display_name(event.tag, "de"),
        ):
            match = matcher.match(name, use_embedding=False)
            if match is not None:
                tags.append(
                    TagAssignment(
                        field=match.field,
                        value=match.value,
                        confidence=round(event.confidence * match.confidence, 4),
                        shot_idx=None,
                        method=match.method,
                        origin="audio",
                    )
                )
                break
    return tags


def dedupe_tags(tags: list[TagAssignment], single_fields: set[str]) -> list[TagAssignment]:
    """Best tag per (field, value, shot); single-valued fields keep one value per shot."""
    best: dict[tuple[str, str, int | None, str], TagAssignment] = {}
    for tag in tags:
        key = (tag.field, tag.value, tag.shot_idx, tag.origin)
        if key not in best or tag.confidence > best[key].confidence:
            best[key] = tag
    result = list(best.values())
    for field in single_fields:
        by_shot: dict[tuple[int | None, str], TagAssignment] = {}
        for tag in [t for t in result if t.field == field]:
            slot = (tag.shot_idx, tag.origin)
            if slot not in by_shot or tag.confidence > by_shot[slot].confidence:
                by_shot[slot] = tag
        result = [t for t in result if t.field != field] + list(by_shot.values())
    return sorted(result, key=lambda t: (t.shot_idx is None, t.shot_idx or 0, t.field, t.value))


def proposals_for(
    unmapped: Sequence[Term], matcher: TaxonomyMatcher, n_descriptions: int, min_confidence: float
) -> list[TermProposal]:
    """Unmapped free terms that recur across shots (or are confident in a single description)."""
    if matcher.empty:  # without an approved vocabulary nothing can be proposed against
        return []
    grouped: dict[str, list[Term]] = defaultdict(list)
    for term in unmapped:
        grouped[normalize(term.text)].append(term)
    needed = 2 if n_descriptions > 1 else 1
    proposals: list[TermProposal] = []
    for key, items in grouped.items():
        shots = {t.shot_idx for t in items}
        confidence = sum(t.confidence for t in items) / len(items)
        if len(shots) < needed or confidence < min_confidence or not key:
            continue
        nearest = matcher.nearest(items[0].text)
        field = (
            nearest[0].field if nearest and nearest[1] >= PROPOSAL_NEAR_FIELD_SIMILARITY else None
        )
        proposals.append(
            TermProposal(field=field, term=items[0].text.strip(), confidence=round(confidence, 4))
        )
    proposals.sort(key=lambda p: -p.confidence)
    return proposals[:MAX_PROPOSALS]


def chunk_transcript(transcript: Transcript, chunk_ms: int) -> list[tuple[int, int, str]]:
    """Consecutive segments grouped into windows of about ``chunk_ms``: ``(start, end, text)``."""
    chunks: list[tuple[int, int, str]] = []
    start: int | None = None
    end = 0
    parts: list[str] = []
    for segment in transcript.segments:
        if start is None:
            start = segment.start_ms
        parts.append(segment.text.strip())
        end = segment.end_ms
        if end - start >= chunk_ms:
            chunks.append((start, end, " ".join(parts)))
            start, parts = None, []
    if start is not None and parts:
        chunks.append((start, end, " ".join(parts)))
    return chunks


def middle_vectors(embed: EmbedOutput | None) -> dict[int | None, NDArray[np.float32]]:
    """The middle keyframe vector per shot (the photo vector for photos)."""
    vectors: dict[int | None, NDArray[np.float32]] = {}
    if embed is None:
        return vectors
    for image in embed.images:
        if image.keyframe in {"middle", "photo"}:
            vectors[image.shot_idx] = from_vector(image.vector)
    return vectors


def disagreement_reasons(
    descriptions: list[VisualDescription],
    vectors: dict[int | None, NDArray[np.float32]],
    zero_shot: ZeroShot,
    settings: WorkerSettings,
) -> list[str]:
    """Where the VLM's shot size / interior-exterior contradicts SigLIP's zero-shot opinion."""
    reasons: list[str] = []
    for description in descriptions:
        vector = vectors.get(description.shot_idx)
        if vector is None:
            continue
        where = "photo" if description.shot_idx is None else f"shot {description.shot_idx}"
        for attribute in ("shot_size", "interior_exterior"):
            estimate = getattr(description, attribute)
            if estimate is None:
                continue
            found = zero_shot.check(
                attribute, str(estimate.value), vector, settings.zero_shot_disagreement
            )
            if found is not None:
                reasons.append(
                    f"vlm_siglip_conflict:{attribute} "
                    f"({where}: vlm={found.vlm}, siglip={found.siglip})"
                )
    return reasons


def vision_descriptions(vision: VisionOutput | None) -> list[VisualDescription]:
    return list(vision.descriptions) if vision else []
