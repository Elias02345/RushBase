"""Backend and profile choice from probe results (thresholds are the ADR-0006/0011 tiers)."""

from rushbase_worker.hardware.model import Backend, Gpu, Profile

XL_MIN_MB = 15_000
GPU8_MIN_MB = 6_900
"""An 8 GB card shows ~7.6 GB free with a desktop on it; 6.9 GB still fits the 4B Q8 profile."""
GPU4_MIN_MB = 3_400
APPLE_USABLE_FRACTION = 0.6
"""Share of unified memory Metal may use (recommendedMaxWorkingSetSize is ~2/3 on small Macs)."""

_ORDER = (Profile.XL, Profile.GPU8, Profile.GPU4, Profile.CPU)


def best_gpu(gpus: list[Gpu]) -> Gpu | None:
    """The accelerator the worker uses: discrete first, then the most free memory."""
    if not gpus:
        return None
    return max(gpus, key=lambda g: (g.discrete, g.vram_free_mb or g.vram_total_mb or 0))


def choose_backend(gpu: Gpu | None, vulkan_devices: list[str], rocm_available: bool) -> Backend:
    """Pick the llama.cpp / whisper.cpp backend (ADR-0008)."""
    if gpu is not None:
        match gpu.vendor:
            case "nvidia":
                return Backend.CUDA
            case "apple":
                return Backend.METAL
            case "amd":
                return Backend.ROCM if rocm_available else Backend.VULKAN
            case "intel":
                return Backend.VULKAN
            case _:
                pass
    return Backend.VULKAN if vulkan_devices else Backend.CPU


def effective_vram_mb(gpu: Gpu | None, ram_total_mb: int | None) -> int | None:
    """Memory available for models: free VRAM, or a share of unified memory on Apple."""
    if gpu is None:
        return None
    if gpu.unified_memory and ram_total_mb:
        return int(ram_total_mb * APPLE_USABLE_FRACTION)
    return gpu.vram_free_mb if gpu.vram_free_mb is not None else gpu.vram_total_mb


def choose_profile(vram_mb: int | None, backend: Backend) -> Profile:
    """Profile from usable accelerator memory; no accelerator means the CPU profile."""
    if backend is Backend.CPU or vram_mb is None:
        return Profile.CPU
    if vram_mb >= XL_MIN_MB:
        return Profile.XL
    if vram_mb >= GPU8_MIN_MB:
        return Profile.GPU8
    if vram_mb >= GPU4_MIN_MB:
        return Profile.GPU4
    return Profile.CPU


def smaller_profile(profile: Profile) -> Profile | None:
    """Next smaller profile for the out-of-memory fallback (``None`` after ``cpu``)."""
    index = _ORDER.index(profile)
    return _ORDER[index + 1] if index + 1 < len(_ORDER) else None
