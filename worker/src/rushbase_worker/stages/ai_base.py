"""Shared plumbing of the AI stages."""

from abc import ABC, abstractmethod

from pydantic import BaseModel

from rushbase_core import Stage
from rushbase_worker.assets.models import ModelNotInstalledError
from rushbase_worker.models import Job
from rushbase_worker.runtimes.llama import LlamaStartError
from rushbase_worker.runtimes.manager import MissingBackendError, Runtimes
from rushbase_worker.stages.base import Requirements, StageContext, StageError


class AiStage(ABC):
    """A stage that needs installed backends and models (see :class:`Runtimes`)."""

    stage: Stage
    requirements = Requirements(ai=True)

    async def run(self, job: Job, ctx: StageContext) -> BaseModel:
        """Process ``job``; missing models/binaries and failed model loads become stage errors."""
        runtimes = ctx.need_runtimes()
        try:
            return await self.process(job, ctx, runtimes)
        except (ModelNotInstalledError, MissingBackendError, LlamaStartError) as exc:
            raise StageError(str(exc)) from exc

    @abstractmethod
    async def process(self, job: Job, ctx: StageContext, runtimes: Runtimes) -> BaseModel:
        """The stage implementation."""
