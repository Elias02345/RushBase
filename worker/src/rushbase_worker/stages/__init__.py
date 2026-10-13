"""Stage handlers and their registry."""

from rushbase_core import Stage
from rushbase_worker.stages.base import Requirements, StageContext, StageError, StageHandler
from rushbase_worker.stages.hash import HashStage
from rushbase_worker.stages.preview import PreviewStage
from rushbase_worker.stages.probe import ProbeStage
from rushbase_worker.stages.shots import ShotsStage
from rushbase_worker.tools.toolbox import Toolbox

HANDLERS: dict[Stage, StageHandler] = {
    Stage.PROBE: ProbeStage(),
    Stage.HASH: HashStage(),
    Stage.PREVIEW: PreviewStage(),
    Stage.SHOTS: ShotsStage(),
}
"""All stages this worker implements (AI stages arrive with milestone M4)."""


def missing_tools(handler: StageHandler, toolbox: Toolbox) -> list[str]:
    """Names of required tools that are not installed."""
    found = {
        "ffmpeg": toolbox.ffmpeg_path,
        "ffprobe": toolbox.ffprobe_path,
        "exiftool": toolbox.exiftool_path,
    }
    return [tool for tool in handler.requirements.tools if found.get(tool) is None]


def runnable_stages(wanted: tuple[Stage, ...], toolbox: Toolbox) -> dict[Stage, StageHandler]:
    """Handlers for ``wanted`` stages whose tool requirements are met and that have no GPU need."""
    return {
        stage: handler
        for stage, handler in HANDLERS.items()
        if stage in wanted and not handler.requirements.gpu and not missing_tools(handler, toolbox)
    }


__all__ = [
    "HANDLERS",
    "Requirements",
    "StageContext",
    "StageError",
    "StageHandler",
    "missing_tools",
    "runnable_stages",
]
