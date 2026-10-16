"""The JSON schema the VLM must follow and the mapping to :class:`VisualDescription`.

The LLM-facing schema is flat: every ``Estimated`` value of the contract becomes a
``<name>`` / ``<name>_confidence`` pair, taxonomy fields become ``tax_<key>`` arrays and review
flags ``flag_<key>`` objects (enums come from the taxonomy snapshot). The grammar derived by
llama.cpp guarantees the shape; pydantic validates the content and the mapping builds the
contract model (source ``ai``).
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from rushbase_core.results import Estimated, Source
from rushbase_core.results_ai import (
    AssetSummary,
    CameraAngle,
    CheckFlag,
    InteriorExterior,
    Labelled,
    ShotSize,
    TaxonomyGuess,
    TimeOfDay,
    Tri,
    VisualDescription,
)
from rushbase_worker.taxonomy import TaxonomySnapshot

MAX_LABELS = 8
MAX_TEXTS = 6
TAX_PREFIX = "tax_"
FLAG_PREFIX = "flag_"

TEXT_ESTIMATES = ("subject", "action", "environment", "lighting")
ENUM_ESTIMATES: dict[str, type[StrEnum]] = {
    "interior_exterior": InteriorExterior,
    "time_of_day": TimeOfDay,
    "shot_size": ShotSize,
    "camera_angle": CameraAngle,
    "person_identifiable": Tri,
}


class SchemaError(ValueError):
    """The model output does not satisfy the schema or the contract."""


def _confidence() -> dict[str, Any]:
    return {"type": "number", "minimum": 0, "maximum": 1}


def _labelled() -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": MAX_LABELS,
        "items": {
            "type": "object",
            "properties": {"label": {"type": "string"}, "confidence": _confidence()},
            "required": ["label", "confidence"],
            "additionalProperties": False,
        },
    }


def _tax_property(values: list[str], single: bool) -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": 1 if single else 5,
        "items": {
            "type": "object",
            "properties": {
                "value": {"type": "string", "enum": values},
                "confidence": _confidence(),
            },
            "required": ["value", "confidence"],
            "additionalProperties": False,
        },
    }


def build_schema(taxonomy: TaxonomySnapshot) -> dict[str, Any]:
    """The strict JSON schema for one shot or photo description."""
    properties: dict[str, Any] = {
        name: {"type": "string"} for name in ("short_de", "short_en", "long_de", "long_en")
    }
    for name in TEXT_ESTIMATES:
        properties[name] = {"type": "string"}
        properties[f"{name}_confidence"] = _confidence()
    for name, enum in ENUM_ESTIMATES.items():
        properties[name] = {"type": "string", "enum": [m.value for m in enum]}
        properties[f"{name}_confidence"] = _confidence()
    properties["people_count"] = {"type": "integer", "minimum": 0, "maximum": 99}
    properties["people_count_confidence"] = _confidence()
    properties["vertical_crop_ok"] = {"type": "boolean"}
    properties["vertical_crop_ok_confidence"] = _confidence()
    properties["text_in_image"] = {
        "type": "array",
        "maxItems": MAX_TEXTS,
        "items": {"type": "string"},
    }
    for name in ("objects", "mood", "quality_issues", "suitability"):
        properties[name] = _labelled()
    for field in taxonomy.fields:
        values = [v.value for v in field.values]
        if values:
            properties[TAX_PREFIX + field.key] = _tax_property(values, field.kind == "single")
    for flag in taxonomy.flags:
        properties[FLAG_PREFIX + flag.key] = {
            "type": "object",
            "properties": {
                "value": {"type": "boolean"},
                "confidence": _confidence(),
                "reason": {"type": "string"},
            },
            "required": ["value", "confidence", "reason"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def summary_schema() -> dict[str, Any]:
    """Schema of the asset summary call."""
    names = ("short_de", "short_en", "long_de", "long_en")
    return {
        "type": "object",
        "properties": {name: {"type": "string"} for name in names},
        "required": list(names),
        "additionalProperties": False,
    }


def _clamp(value: object) -> object:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return value
    return min(1.0, max(0.0, float(value)))


class _Labelled(BaseModel):
    label: str
    confidence: float = Field(ge=0, le=1)

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamped(cls, value: object) -> object:
        return _clamp(value)


class LlmDescription(BaseModel):
    """Validated model output (flat); taxonomy and flag properties arrive as extras."""

    model_config = ConfigDict(extra="allow")

    short_de: str = Field(min_length=1)
    short_en: str = Field(min_length=1)
    long_de: str = Field(min_length=1)
    long_en: str = Field(min_length=1)
    subject: str = ""
    action: str = ""
    environment: str = ""
    lighting: str = ""
    interior_exterior: InteriorExterior = InteriorExterior.UNKNOWN
    time_of_day: TimeOfDay = TimeOfDay.UNKNOWN
    shot_size: ShotSize | None = None
    camera_angle: CameraAngle | None = None
    person_identifiable: Tri = Tri.UNKNOWN
    people_count: int = Field(default=0, ge=0)
    vertical_crop_ok: bool | None = None
    text_in_image: list[str] = []
    objects: list[_Labelled] = []
    mood: list[_Labelled] = []
    quality_issues: list[_Labelled] = []
    suitability: list[_Labelled] = []
    subject_confidence: float = Field(default=0.0, ge=0, le=1)
    action_confidence: float = Field(default=0.0, ge=0, le=1)
    environment_confidence: float = Field(default=0.0, ge=0, le=1)
    lighting_confidence: float = Field(default=0.0, ge=0, le=1)
    interior_exterior_confidence: float = Field(default=0.0, ge=0, le=1)
    time_of_day_confidence: float = Field(default=0.0, ge=0, le=1)
    shot_size_confidence: float = Field(default=0.0, ge=0, le=1)
    camera_angle_confidence: float = Field(default=0.0, ge=0, le=1)
    person_identifiable_confidence: float = Field(default=0.0, ge=0, le=1)
    people_count_confidence: float = Field(default=0.0, ge=0, le=1)
    vertical_crop_ok_confidence: float = Field(default=0.0, ge=0, le=1)

    @field_validator("*", mode="before")
    @classmethod
    def _clamp_confidences(cls, value: object, info: Any) -> object:
        return _clamp(value) if str(info.field_name).endswith("_confidence") else value


def parse_description(text: str) -> LlmDescription:
    """Parse and validate the model's JSON output."""
    try:
        return LlmDescription.model_validate_json(text)
    except ValidationError as exc:
        raise SchemaError(str(exc)[:500]) from exc


def _estimate(value: Any, confidence: float) -> Estimated[Any]:
    return Estimated(value=value, source=Source.AI, confidence=confidence)


def to_visual_description(
    raw: LlmDescription, shot_idx: int | None, taxonomy: TaxonomySnapshot
) -> VisualDescription:
    """Map the flat model output to the contract model (``Estimated`` pairs, taxonomy, flags)."""
    extras = raw.model_extra or {}
    guesses: list[TaxonomyGuess] = []
    for field in taxonomy.fields:
        allowed = {v.value for v in field.values}
        for item in extras.get(TAX_PREFIX + field.key, []):
            value = item.get("value") if isinstance(item, dict) else None
            if value in allowed:
                confidence = _clamp(item.get("confidence", 0.0))
                guesses.append(
                    TaxonomyGuess(field=field.key, value=value, confidence=float(confidence))  # type: ignore[arg-type]
                )
    flags: list[CheckFlag] = []
    for flag in taxonomy.flags:
        item = extras.get(FLAG_PREFIX + flag.key)
        if isinstance(item, dict) and isinstance(item.get("value"), bool):
            flags.append(
                CheckFlag(
                    flag=flag.key,
                    value=item["value"],
                    confidence=float(_clamp(item.get("confidence", 0.0))),  # type: ignore[arg-type]
                    reason=(str(item.get("reason")).strip() or None)
                    if item.get("reason")
                    else None,
                )
            )

    def text_estimate(name: str) -> Estimated[str] | None:
        value = getattr(raw, name).strip()
        return _estimate(value, getattr(raw, f"{name}_confidence")) if value else None

    def labelled(items: list[_Labelled]) -> list[Labelled]:
        return [
            Labelled(label=i.label.strip(), confidence=i.confidence)
            for i in items
            if i.label.strip()
        ]

    return VisualDescription(
        shot_idx=shot_idx,
        short_de=raw.short_de.strip(),
        short_en=raw.short_en.strip(),
        long_de=raw.long_de.strip(),
        long_en=raw.long_en.strip(),
        subject=text_estimate("subject"),
        action=text_estimate("action"),
        environment=text_estimate("environment"),
        lighting=text_estimate("lighting"),
        interior_exterior=_estimate(raw.interior_exterior, raw.interior_exterior_confidence),
        time_of_day=_estimate(raw.time_of_day, raw.time_of_day_confidence),
        shot_size=_estimate(raw.shot_size, raw.shot_size_confidence) if raw.shot_size else None,
        camera_angle=(
            _estimate(raw.camera_angle, raw.camera_angle_confidence) if raw.camera_angle else None
        ),
        people_count=_estimate(raw.people_count, raw.people_count_confidence),
        person_identifiable=_estimate(raw.person_identifiable, raw.person_identifiable_confidence),
        text_in_image=[t.strip() for t in raw.text_in_image if t.strip()],
        objects=labelled(raw.objects),
        mood=labelled(raw.mood),
        quality_issues=labelled(raw.quality_issues),
        suitability=labelled(raw.suitability),
        vertical_crop_ok=(
            _estimate(raw.vertical_crop_ok, raw.vertical_crop_ok_confidence)
            if raw.vertical_crop_ok is not None
            else None
        ),
        taxonomy=guesses,
        flags=flags,
    )


def parse_summary(text: str) -> AssetSummary:
    """Parse and validate the summary call output."""
    try:
        return AssetSummary.model_validate_json(text)
    except ValidationError as exc:
        raise SchemaError(str(exc)[:500]) from exc
