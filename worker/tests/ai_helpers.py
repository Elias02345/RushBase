"""Shared fixtures of the AI stage tests."""

from typing import Any

from rushbase_worker.taxonomy import TaxonomyField, TaxonomyFlag, TaxonomySnapshot, TaxonomyValue

TAXONOMY = TaxonomySnapshot(
    version=3,
    fields=[
        TaxonomyField(key="location", label_de="Ort", label_en="Location", kind="single",
                      values=[TaxonomyValue(value="beach"), TaxonomyValue(value="forest")]),
        TaxonomyField(key="topics", label_de="Themen", label_en="Topics", kind="multi",
                      values=[TaxonomyValue(value="sport", synonyms=["sports"])]),
    ],
    flags=[
        TaxonomyFlag(key="critical", description_de="kritisch", description_en="critical scene")
    ],
)  # fmt: skip


def valid_answer(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "short_de": "Ein Strand.", "short_en": "A beach.", "long_de": "Ein Strand bei Tag.",
        "long_en": "A beach by day.", "subject": "beach", "subject_confidence": 0.9,
        "action": "", "action_confidence": 0.0, "environment": "coast",
        "environment_confidence": 0.8,
        "lighting": "sunlight", "lighting_confidence": 0.7, "interior_exterior": "exterior",
        "interior_exterior_confidence": 0.95, "time_of_day": "day", "time_of_day_confidence": 0.8,
        "shot_size": "wide", "shot_size_confidence": 0.85, "camera_angle": "eye_level",
        "camera_angle_confidence": 0.8, "person_identifiable": "no",
        "person_identifiable_confidence": 0.9, "people_count": 2, "people_count_confidence": 0.7,
        "vertical_crop_ok": True, "vertical_crop_ok_confidence": 0.6, "text_in_image": [],
        "objects": [{"label": "sand", "confidence": 0.9}], "mood": [], "quality_issues": [],
        "suitability": [{"label": "b-roll", "confidence": 0.8}],
        "tax_location": [{"value": "beach", "confidence": 0.9}],
        "tax_topics": [{"value": "sport", "confidence": 0.4}],
        "flag_critical": {"value": False, "confidence": 0.9, "reason": ""},
    }  # fmt: skip
    return {**base, **overrides}
