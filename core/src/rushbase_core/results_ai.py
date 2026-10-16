"""Stage result contracts of the AI stages (``audio``, ``embed``, ``vision``, ``classify``).

Same rules as :mod:`rushbase_core.results`: workers produce these, the server validates and
stores them. Every AI value carries a confidence; every output records the model that made it
(``ModelRef``) so results can be compared, re-run selectively and never overwrite human or
Claude edits (precedence human > claude > ai is enforced by the server).

Vectors travel as base64 of little-endian float32 (:class:`Vector`) to keep payloads compact;
they are always L2-normalised by the producer.
"""

import base64
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rushbase_core.results import Estimated


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ModelRef(_Model):
    """Which model produced a result (stored with every result for reprocessing decisions)."""

    model_id: str
    """Stable id, e.g. ``qwen3.5-9b-q4_k_m`` or ``siglip2-base-patch16-256``."""
    revision: str
    """Upstream revision / file hash prefix; changes force a re-index."""
    profile: str | None = None
    """Hardware profile used (``xl``, ``gpu8``, ``gpu4``, ``cpu``) — the "analysis quality"."""
    prompt_version: str | None = None


class Vector(_Model):
    """An L2-normalised embedding as base64 little-endian float32."""

    dim: int = Field(ge=1, le=4096)
    b64: str

    @model_validator(mode="after")
    def _check_length(self) -> "Vector":
        if len(base64.b64decode(self.b64, validate=True)) != self.dim * 4:
            raise ValueError("vector payload does not match dim")
        return self


# --- audio -----------------------------------------------------------------------------------


class Word(_Model):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str
    probability: float | None = Field(default=None, ge=0.0, le=1.0)


class TranscriptSegment(_Model):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str
    language: str | None = None
    speaker: str | None = None
    words: list[Word] = Field(default_factory=list)


class Transcript(_Model):
    language: str | None = None
    language_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    segments: list[TranscriptSegment]
    model: ModelRef


class AudioEvent(_Model):
    """A grouped event (``gunshot``, ``explosion``, ``speech``, ``music``, ``applause``, ``wind``,
    ``silence``) with the raw classifier labels kept for later re-thresholding."""

    tag: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)
    raw_top: list[tuple[str, float]] = Field(default_factory=list, max_length=5)


class AudioType(StrEnum):
    MUSIC = "music"
    SPEECH = "speech"
    AMBIENCE = "ambience"
    SFX = "sfx"
    MIXED = "mixed"
    SILENCE = "silence"


class Loudness(_Model):
    integrated_lufs: float | None = None
    true_peak_dbtp: float | None = None
    lra_lu: float | None = None


class AudioOutput(_Model):
    schema_: Literal[1] = Field(default=1, alias="schema")
    has_audio: bool
    transcript: Transcript | None = None
    events: list[AudioEvent] = Field(default_factory=list)
    events_model: ModelRef | None = None
    loudness: Loudness | None = None
    audio_type: Estimated[AudioType] | None = None
    """For pure audio assets: music, voice-over, ambience, SFX."""
    bpm: float | None = None
    musical_key: str | None = None


# --- embed (image vectors of keyframes and photos) -------------------------------------------


class ImageEmbedding(_Model):
    shot_idx: int | None = Field(default=None, ge=0)
    """``None`` for photos (the whole image)."""
    keyframe: Literal["start", "middle", "end", "photo"]
    vector: Vector


class EmbedOutput(_Model):
    schema_: Literal[1] = Field(default=1, alias="schema")
    model: ModelRef
    images: list[ImageEmbedding]


# --- vision ----------------------------------------------------------------------------------


class InteriorExterior(StrEnum):
    INTERIOR = "interior"
    EXTERIOR = "exterior"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class TimeOfDay(StrEnum):
    DAWN = "dawn"
    DAY = "day"
    GOLDEN_HOUR = "golden_hour"
    DUSK = "dusk"
    NIGHT = "night"
    UNKNOWN = "unknown"


class ShotSize(StrEnum):
    EXTREME_WIDE = "extreme_wide"
    WIDE = "wide"
    FULL = "full"
    MEDIUM = "medium"
    MEDIUM_CLOSE = "medium_close"
    CLOSE_UP = "close_up"
    EXTREME_CLOSE_UP = "extreme_close_up"
    DETAIL = "detail"


class CameraAngle(StrEnum):
    EYE_LEVEL = "eye_level"
    LOW = "low"
    HIGH = "high"
    BIRDS_EYE = "birds_eye"
    TOP_DOWN = "top_down"
    DUTCH = "dutch"
    POV = "pov"


class Tri(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class Labelled(_Model):
    """A free-text label with confidence (objects, moods, quality issues, suitability)."""

    label: str
    confidence: float = Field(ge=0.0, le=1.0)


class TaxonomyGuess(_Model):
    """A value the VLM chose for a configured taxonomy field (mapped again in ``classify``)."""

    field: str
    value: str
    confidence: float = Field(ge=0.0, le=1.0)


class CheckFlag(_Model):
    """A configured review flag (e.g. "critical scene per guideline")."""

    flag: str
    value: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str | None = None


class VisualDescription(_Model):
    """Analysis of one shot (video) or of a photo — spec §6 "detailed content description"."""

    shot_idx: int | None = Field(default=None, ge=0)
    short_de: str
    short_en: str
    long_de: str
    long_en: str
    subject: Estimated[str] | None = None
    action: Estimated[str] | None = None
    environment: Estimated[str] | None = None
    interior_exterior: Estimated[InteriorExterior] | None = None
    time_of_day: Estimated[TimeOfDay] | None = None
    lighting: Estimated[str] | None = None
    shot_size: Estimated[ShotSize] | None = None
    camera_angle: Estimated[CameraAngle] | None = None
    people_count: Estimated[int] | None = None
    person_identifiable: Estimated[Tri] | None = None
    text_in_image: list[str] = Field(default_factory=list)
    objects: list[Labelled] = Field(default_factory=list)
    mood: list[Labelled] = Field(default_factory=list)
    quality_issues: list[Labelled] = Field(default_factory=list)
    suitability: list[Labelled] = Field(default_factory=list)
    """E.g. ``b-roll``, ``opener``, ``transition``, ``text_background``."""
    vertical_crop_ok: Estimated[bool] | None = None
    taxonomy: list[TaxonomyGuess] = Field(default_factory=list)
    flags: list[CheckFlag] = Field(default_factory=list)


class AssetSummary(_Model):
    short_de: str
    short_en: str
    long_de: str
    long_en: str


class VisionOutput(_Model):
    schema_: Literal[1] = Field(default=1, alias="schema")
    model: ModelRef
    descriptions: list[VisualDescription]
    summary: AssetSummary | None = None


# --- classify (CPU: taxonomy mapping, text embeddings, proposals) -----------------------------


class TextEmbedding(_Model):
    kind: Literal["asset_summary", "shot_description", "transcript_chunk"]
    shot_idx: int | None = Field(default=None, ge=0)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    language: Literal["de", "en"] | None = None
    vector: Vector


class TagAssignment(_Model):
    field: str
    value: str
    """Canonical (approved) taxonomy value."""
    confidence: float = Field(ge=0.0, le=1.0)
    shot_idx: int | None = Field(default=None, ge=0)
    method: Literal["exact", "synonym", "embedding", "rule"]
    origin: Literal["vision", "audio", "metadata"]


class TermProposal(_Model):
    field: str | None
    term: str
    confidence: float = Field(ge=0.0, le=1.0)


class ClassifyOutput(_Model):
    schema_: Literal[1] = Field(default=1, alias="schema")
    text_model: ModelRef
    taxonomy_version: int
    """Version of the taxonomy snapshot the worker used (sent with the job)."""
    tags: list[TagAssignment] = Field(default_factory=list)
    proposals: list[TermProposal] = Field(default_factory=list)
    text_embeddings: list[TextEmbedding] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
    """Why the asset should go to the review list (low confidence, VLM vs. SigLIP conflict …)."""
