"""Hardware detection and the choice of inference backend and hardware profile (ADR-0011)."""

from rushbase_worker.hardware.detect import detect_hardware, free_vram_mb
from rushbase_worker.hardware.model import Backend, CpuInfo, Gpu, HardwareReport, Profile
from rushbase_worker.hardware.profile import choose_backend, choose_profile, smaller_profile

__all__ = [
    "Backend",
    "CpuInfo",
    "Gpu",
    "HardwareReport",
    "Profile",
    "choose_backend",
    "choose_profile",
    "detect_hardware",
    "free_vram_mb",
    "smaller_profile",
]
