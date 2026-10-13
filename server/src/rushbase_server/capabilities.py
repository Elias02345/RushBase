"""What a worker can do; stored on the worker row and used to filter claimed jobs."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rushbase_core import SERVER_STAGES, Stage

CPU_ONLY_BACKENDS = frozenset({"cpu"})


class ModelInfo(BaseModel):
    """An installed model for a stage."""

    model_id: str
    version: str


class WorkerCapabilities(BaseModel):
    """Capabilities a worker reports at enrollment and with every claim."""

    model_config = ConfigDict(extra="forbid")

    backends: list[str] = Field(default_factory=lambda: ["cpu"], description="cuda, vulkan, ...")
    vram_mb: int | None = Field(default=None, ge=0)
    ram_mb: int | None = Field(default=None, ge=0)
    stages: list[Stage] = Field(default_factory=list, description="Enabled stages.")
    models: dict[str, ModelInfo] = Field(default_factory=dict, description="Per stage.")
    profile: str | None = None

    @property
    def has_gpu(self) -> bool:
        """Whether any non-CPU backend is available."""
        return any(b.lower() not in CPU_ONLY_BACKENDS for b in self.backends)

    @property
    def claimable_stages(self) -> list[Stage]:
        """Stages a remote worker may claim (server-side stages are excluded)."""
        return [s for s in self.stages if s not in SERVER_STAGES]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "WorkerCapabilities":
        """Parse the JSON stored in ``worker.capabilities`` (empty means no capabilities)."""
        return cls.model_validate(data) if data else cls()
