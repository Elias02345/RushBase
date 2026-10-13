"""Processing pipeline: stages, their dependencies and the per-asset job plan.

The plan is a pure function so that the server (job creation), the CLI (``rushbase jobs``)
and tests share one definition. Disabled stages are not created; dependencies on them are
replaced by the dependencies of the skipped stage, so the DAG stays connected.
"""

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from rushbase_core.media import MediaType


class Stage(StrEnum):
    """Processing stages (``job.stage``)."""

    PROBE = "probe"
    HASH = "hash"
    PREVIEW = "preview"
    SHOTS = "shots"
    AUDIO = "audio"
    EMBED = "embed"
    VISION = "vision"
    CLASSIFY = "classify"
    FINALIZE = "finalize"


ALL_STAGES: Final = tuple(Stage)
SERVER_STAGES: Final = frozenset({Stage.FINALIZE})
"""Stages the server executes itself; remote workers never receive them."""
DEFAULT_ENABLED_STAGES: Final = (
    Stage.PROBE,
    Stage.HASH,
    Stage.PREVIEW,
    Stage.SHOTS,
    Stage.FINALIZE,
)
"""Technical stages. The AI stages (audio, embed, vision, classify) arrive with milestone M4."""

# probe and hash run in parallel; everything else follows the specification's order.
STAGE_DEPENDENCIES: Final[dict[Stage, tuple[Stage, ...]]] = {
    Stage.PROBE: (),
    Stage.HASH: (),
    Stage.PREVIEW: (Stage.PROBE,),
    Stage.SHOTS: (Stage.PROBE, Stage.PREVIEW),
    Stage.AUDIO: (Stage.PROBE,),
    Stage.EMBED: (Stage.PREVIEW, Stage.SHOTS),
    Stage.VISION: (Stage.SHOTS,),
    Stage.CLASSIFY: (Stage.AUDIO, Stage.EMBED, Stage.VISION),
    Stage.FINALIZE: (Stage.CLASSIFY, Stage.HASH),
}

_NOT_APPLICABLE: Final[dict[MediaType, frozenset[Stage]]] = {
    MediaType.VIDEO: frozenset(),
    MediaType.PHOTO: frozenset({Stage.SHOTS, Stage.AUDIO}),
    MediaType.AUDIO: frozenset({Stage.SHOTS, Stage.VISION}),
    MediaType.OTHER: frozenset(
        {Stage.PREVIEW, Stage.SHOTS, Stage.AUDIO, Stage.EMBED, Stage.VISION, Stage.CLASSIFY}
    ),
}


@dataclass(frozen=True)
class JobSpec:
    """A job to create: its stage and the stages it waits for."""

    stage: Stage
    depends_on: tuple[Stage, ...]


def parse_stages(names: Collection[str]) -> tuple[Stage, ...]:
    """Convert stage names to :class:`Stage`, raising ``ValueError`` for unknown names."""
    return tuple(Stage(name.strip()) for name in names if name.strip())


def build_job_plan(
    enabled: Collection[Stage | str], media_type: MediaType = MediaType.VIDEO
) -> list[JobSpec]:
    """Return the jobs for an asset of ``media_type`` in dependency order.

    ``finalize`` is always part of the plan. Stages that do not apply to the media type
    (for example ``shots`` for photos) are dropped like disabled stages.
    """
    active = {Stage(s) for s in enabled} | {Stage.FINALIZE}
    active -= _NOT_APPLICABLE[media_type]

    def resolve(stage: Stage) -> set[Stage]:
        result: set[Stage] = set()
        for dep in STAGE_DEPENDENCIES[stage]:
            if dep in active:
                result.add(dep)
            else:
                result |= resolve(dep)
        return result

    plan = [
        JobSpec(stage, tuple(sorted(resolve(stage), key=ALL_STAGES.index)))
        for stage in ALL_STAGES
        if stage in active
    ]
    return plan
