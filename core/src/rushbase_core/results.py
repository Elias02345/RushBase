"""Stage result contracts shared by workers (producers) and the server (consumer).

Every worker stage submits ``ResultIn.output`` as the JSON form of one of the models below.
The server validates it with :func:`parse_stage_output` before storing it, normalises the
fields into asset/shot columns and writes them into sidecars. Bump ``schema`` of a model when
its shape changes incompatibly; the server keeps accepting older versions.

Conventions: times in milliseconds, sizes in bytes, paths relative to the library root with
``/`` separators. Every normalised value may be ``None`` when the file does not provide it;
the complete raw tool output is kept in :attr:`ProbeOutput.raw`.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from rushbase_core.pipeline import Stage


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Source(StrEnum):
    """Where a value came from (precedence for edits: human > claude > ai > metadata)."""

    METADATA = "metadata"
    AI = "ai"
    CLAUDE = "claude"
    HUMAN = "human"


class Estimated[T](_Model):
    """A value that may come from metadata or be estimated by analysis."""

    value: T
    source: Source = Source.METADATA
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


# --- probe -----------------------------------------------------------------------------------


class CameraInfo(_Model):
    make: str | None = None
    model: str | None = None
    serial: str | None = None
    firmware: str | None = None
    recording_format: str | None = None
    sensor_mode: str | None = None


class LensInfo(_Model):
    model: str | None = None
    focal_length_mm: float | None = None
    focal_length_35mm: float | None = None
    aperture: float | None = None
    t_stop: float | None = None
    focus_distance_m: float | None = None
    stabilization: bool | None = None
    adapter: str | None = None


class ExposureInfo(_Model):
    iso: int | None = None
    shutter_s: float | None = None
    shutter_angle: float | None = None
    white_balance_k: int | None = None
    tint: float | None = None
    nd_filter: str | None = None
    gamma: str | None = None
    log_profile: str | None = None
    color_space: str | None = None
    embedded_lut: str | None = None
    hdr: str | None = None


class VideoStreamInfo(_Model):
    codec: str | None = None
    profile: str | None = None
    bit_depth: int | None = None
    chroma_subsampling: str | None = None
    bitrate: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    capture_fps: float | None = None
    """Sensor frame rate when it differs from playback ``fps`` (off-speed / slow motion)."""
    rotation: int | None = None
    timecode: str | None = None


class AudioStreamInfo(_Model):
    codec: str | None = None
    channels: int | None = None
    sample_rate: int | None = None
    bit_depth: int | None = None
    track_names: list[str] = Field(default_factory=list)
    recorder: str | None = None
    microphone: str | None = None
    timecode: str | None = None


class LocationInfo(_Model):
    lat: float | None = None
    lon: float | None = None
    alt_m: float | None = None
    heading_deg: float | None = None
    speed_mps: float | None = None
    track_rel_path: str | None = None
    """Derived telemetry track (e.g. drone flight path) under ``.rushbase/derived``."""


class AttitudeInfo(_Model):
    pitch_deg: float | None = None
    roll_deg: float | None = None
    yaw_deg: float | None = None


class PhotoInfo(_Model):
    flash: str | None = None
    bracket_group: str | None = None
    burst_group: str | None = None
    rating: int | None = None


class Orientation(StrEnum):
    LANDSCAPE = "landscape"
    PORTRAIT = "portrait"
    SQUARE = "square"


class DecodeStatus(StrEnum):
    """ADR-0003 decode status."""

    OK = "ok"
    PROXY_ONLY = "proxy_only"
    METADATA_ONLY = "metadata_only"
    DECODER_MISSING = "decoder_missing"
    DECODER_ERROR = "decoder_error"


class ProbeOutput(_Model):
    """Output of the ``probe`` stage (normalised fields + complete raw tool output)."""

    schema_: Literal[1] = Field(default=1, alias="schema")
    container: str | None = None
    duration_ms: int | None = None
    capture_at: datetime | None = None
    capture_tz: str | None = None
    reel: str | None = None
    clip_name: str | None = None
    scene: str | None = None
    take: str | None = None
    orientation: Orientation | None = None
    camera: CameraInfo = Field(default_factory=CameraInfo)
    lens: LensInfo = Field(default_factory=LensInfo)
    exposure: ExposureInfo = Field(default_factory=ExposureInfo)
    video: VideoStreamInfo | None = None
    audio: AudioStreamInfo | None = None
    location: LocationInfo | None = None
    attitude: AttitudeInfo | None = None
    photo: PhotoInfo | None = None
    decode_status: DecodeStatus = DecodeStatus.OK
    decoder_hint: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    """Raw output per tool: ``{"ffprobe": ..., "exiftool": ..., "sidecar_xml": ...}``."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


# --- hash ------------------------------------------------------------------------------------


class FileHash(_Model):
    rel_path: str
    size: int
    blake3: str


class HashOutput(_Model):
    schema_: Literal[1] = Field(default=1, alias="schema")
    files: list[FileHash]

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


# --- preview ---------------------------------------------------------------------------------


class DerivedFile(_Model):
    """A file the worker wrote under ``.rushbase/derived/<asset-id>/``."""

    kind: Literal["thumbnail", "poster", "proxy", "waveform", "photo_preview", "keyframe", "other"]
    rel_path: str
    width: int | None = None
    height: int | None = None
    size: int | None = None
    mime: str | None = None


class PreviewOutput(_Model):
    schema_: Literal[1] = Field(default=1, alias="schema")
    files: list[DerivedFile]
    normalization: str | None = None
    """LUT/transform applied for log material (``None`` = none applied)."""
    perceptual_hash: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


# --- shots -----------------------------------------------------------------------------------


class CameraMotion(StrEnum):
    STATIC = "static"
    PAN = "pan"
    TILT = "tilt"
    ZOOM = "zoom"
    HANDHELD = "handheld"
    MIXED = "mixed"


class Keyframe(_Model):
    t_ms: int
    rel_path: str
    position: Literal["start", "middle", "end"]


class ShotOut(_Model):
    idx: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    keyframes: list[Keyframe]
    camera_motion: Estimated[CameraMotion] | None = None
    sharpness: float | None = Field(default=None, ge=0.0, le=1.0)
    shake: float | None = Field(default=None, ge=0.0, le=1.0)
    exposure: float | None = Field(default=None, ge=-1.0, le=1.0)
    """-1 = heavily underexposed, 0 = balanced, 1 = heavily overexposed."""
    noise: float | None = Field(default=None, ge=0.0, le=1.0)


class ShotsOutput(_Model):
    schema_: Literal[1] = Field(default=1, alias="schema")
    detector: str
    shots: list[ShotOut]

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


# --- registry --------------------------------------------------------------------------------

STAGE_OUTPUT_MODELS: dict[Stage, type[BaseModel]] = {
    Stage.PROBE: ProbeOutput,
    Stage.HASH: HashOutput,
    Stage.PREVIEW: PreviewOutput,
    Stage.SHOTS: ShotsOutput,
}
"""Stages with a typed contract. AI stages are added in milestone M4."""


def parse_stage_output(stage: Stage, output: dict[str, Any]) -> BaseModel | None:
    """Validate ``output`` for ``stage``; returns ``None`` for stages without a contract yet."""
    model = STAGE_OUTPUT_MODELS.get(stage)
    return None if model is None else model.model_validate(output)
