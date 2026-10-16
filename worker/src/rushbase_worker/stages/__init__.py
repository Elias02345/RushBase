"""Stage handlers and their registry."""

from rushbase_core import Stage
from rushbase_worker.runtimes.manager import Runtimes
from rushbase_worker.stages.audio import AudioStage
from rushbase_worker.stages.base import Requirements, StageContext, StageError, StageHandler
from rushbase_worker.stages.classify import ClassifyStage
from rushbase_worker.stages.embed import EmbedStage
from rushbase_worker.stages.hash import HashStage
from rushbase_worker.stages.preview import PreviewStage
from rushbase_worker.stages.probe import ProbeStage
from rushbase_worker.stages.shots import ShotsStage
from rushbase_worker.stages.vision import VisionStage
from rushbase_worker.tools.toolbox import Toolbox

HANDLERS: dict[Stage, StageHandler] = {
    Stage.PROBE: ProbeStage(),
    Stage.HASH: HashStage(),
    Stage.PREVIEW: PreviewStage(),
    Stage.SHOTS: ShotsStage(),
    Stage.AUDIO: AudioStage(),
    Stage.EMBED: EmbedStage(),
    Stage.VISION: VisionStage(),
    Stage.CLASSIFY: ClassifyStage(),
}
"""All stages this worker implements."""


def missing_tools(handler: StageHandler, toolbox: Toolbox) -> list[str]:
    """Names of required tools that are not installed."""
    found = {
        "ffmpeg": toolbox.ffmpeg_path,
        "ffprobe": toolbox.ffprobe_path,
        "exiftool": toolbox.exiftool_path,
    }
    return [tool for tool in handler.requirements.tools if found.get(tool) is None]


def missing_requirements(
    handler: StageHandler, toolbox: Toolbox, runtimes: Runtimes | None
) -> list[str]:
    """Everything that keeps ``handler`` from running here (tools, backends, models)."""
    problems = missing_tools(handler, toolbox)
    if handler.requirements.ai:
        problems += runtimes.missing_assets(handler.stage) if runtimes else ["AI runtimes"]
    elif handler.requirements.gpu:
        problems.append("gpu")
    return problems


def runnable_stages(
    wanted: tuple[Stage, ...], toolbox: Toolbox, runtimes: Runtimes | None = None
) -> dict[Stage, StageHandler]:
    """Handlers for ``wanted`` stages whose tools, backends and models are available."""
    return {
        stage: handler
        for stage, handler in HANDLERS.items()
        if stage in wanted and not missing_requirements(handler, toolbox, runtimes)
    }


__all__ = [
    "HANDLERS",
    "Requirements",
    "StageContext",
    "StageError",
    "StageHandler",
    "missing_requirements",
    "missing_tools",
    "runnable_stages",
]
