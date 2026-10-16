"""Review reasons: low confidence per field, true check flags, VLM vs. SigLIP conflicts."""

from collections.abc import Callable

from rushbase_core.results import Estimated
from rushbase_core.results_ai import VisualDescription

REVIEWED_ESTIMATES = (
    "subject",
    "action",
    "environment",
    "lighting",
    "interior_exterior",
    "time_of_day",
    "shot_size",
    "camera_angle",
    "people_count",
    "person_identifiable",
)
UNKNOWN_VALUES = {"unknown"}


def _where(description: VisualDescription) -> str:
    return "photo" if description.shot_idx is None else f"shot {description.shot_idx}"


def low_confidence_reasons(
    descriptions: list[VisualDescription], threshold_for: Callable[[str], float]
) -> list[str]:
    """One reason per (field, shot) whose confidence is below the field's threshold."""
    reasons: list[str] = []
    for description in descriptions:
        for name in REVIEWED_ESTIMATES:
            estimate: Estimated[object] | None = getattr(description, name)
            if estimate is None or str(estimate.value) in UNKNOWN_VALUES:
                continue
            if estimate.confidence is not None and estimate.confidence < threshold_for(name):
                reasons.append(f"low_confidence:{name} ({_where(description)})")
        for guess in description.taxonomy:
            if guess.confidence < threshold_for(guess.field):
                reasons.append(f"low_confidence:{guess.field} ({_where(description)})")
        for flag in description.flags:
            if flag.value:
                reasons.append(f"flag:{flag.flag} ({_where(description)})")
            if flag.confidence < threshold_for(f"flag_{flag.flag}"):
                reasons.append(f"low_confidence:flag_{flag.flag} ({_where(description)})")
    return list(dict.fromkeys(reasons))
