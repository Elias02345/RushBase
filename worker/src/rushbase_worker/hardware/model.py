"""Result types of the hardware probe."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Backend(StrEnum):
    """Inference backends llama.cpp / whisper.cpp are available for."""

    CUDA = "cuda"
    ROCM = "rocm"
    VULKAN = "vulkan"
    SYCL = "sycl"
    METAL = "metal"
    CPU = "cpu"


class Profile(StrEnum):
    """Hardware profiles (ADR-0006/0011), ordered from large to small."""

    XL = "xl"
    GPU8 = "gpu8"
    GPU4 = "gpu4"
    CPU = "cpu"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class Gpu(_Model):
    """One accelerator as seen by a vendor tool, sysfs or Vulkan."""

    vendor: Literal["nvidia", "amd", "intel", "apple", "other"]
    name: str
    vram_total_mb: int | None = None
    vram_free_mb: int | None = None
    """Free memory right now (the desktop and other programs use some)."""
    driver: str | None = None
    cuda_version: str | None = None
    compute_capability: str | None = None
    discrete: bool = True
    unified_memory: bool = False
    source: str = ""
    """Which probe reported it (``nvml``, ``nvidia-smi``, ``rocm-smi``, ``sysfs``, ``sysctl``)."""


class CpuInfo(_Model):
    model: str
    cores: int
    avx2: bool = False
    avx512: bool = False
    neon: bool = False


class HardwareReport(_Model):
    """Everything the worker knows about the machine it runs on."""

    os: str
    arch: str
    cpu: CpuInfo
    ram_total_mb: int | None
    ram_available_mb: int | None
    gpus: list[Gpu]
    vulkan_devices: list[str]
    backend: Backend
    profile: Profile
    effective_vram_mb: int | None
    """Memory (free right now) the profile choice is based on; ``None`` without accelerator."""
    notes: list[str] = Field(default_factory=list)
