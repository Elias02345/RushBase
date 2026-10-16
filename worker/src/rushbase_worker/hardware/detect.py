"""Probe the machine: accelerators, CPU features, RAM and Vulkan devices."""

import os
import platform
import shutil
import subprocess
from pathlib import Path

from rushbase_worker.hardware.model import CpuInfo, Gpu, HardwareReport
from rushbase_worker.hardware.parse import (
    parse_cpuinfo,
    parse_cuda_version,
    parse_meminfo,
    parse_nvidia_smi_csv,
    parse_rocm_smi_json,
    parse_vulkaninfo_summary,
)
from rushbase_worker.hardware.profile import (
    best_gpu,
    choose_backend,
    choose_profile,
    effective_vram_mb,
)

_PCI_VENDORS = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}


def _run(args: list[str], timeout: float = 15.0) -> str | None:
    """Output of a tool, or ``None`` when it is missing or fails."""
    exe = shutil.which(args[0])
    if exe is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603
            [exe, *args[1:]], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def nvidia_gpus() -> list[Gpu]:
    """NVIDIA GPUs via NVML (``nvidia-ml-py``, optional) or ``nvidia-smi``."""
    try:
        found = _nvml_gpus()
    except Exception:
        found = []
    if found:
        return found
    query = _run(
        ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version,compute_cap",
         "--format=csv,noheader,nounits"]
    )  # fmt: skip
    if not query:
        return []
    header = _run(["nvidia-smi"]) or ""
    return parse_nvidia_smi_csv(query, parse_cuda_version(header))


def _nvml_gpus() -> list[Gpu]:
    import pynvml

    pynvml.nvmlInit()
    try:
        driver = pynvml.nvmlSystemGetDriverVersion()
        cuda = pynvml.nvmlSystemGetCudaDriverVersion()
        gpus = []
        for index in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(handle)
            name = pynvml.nvmlDeviceGetName(handle)
            gpus.append(
                Gpu(
                    vendor="nvidia",
                    name=name.decode() if isinstance(name, bytes) else str(name),
                    vram_total_mb=memory.total // (1024 * 1024),
                    vram_free_mb=memory.free // (1024 * 1024),
                    driver=driver.decode() if isinstance(driver, bytes) else str(driver),
                    cuda_version=f"{cuda // 1000}.{(cuda % 1000) // 10}",
                    compute_capability=f"{major}.{minor}",
                    source="nvml",
                )
            )
        return gpus
    finally:
        pynvml.nvmlShutdown()


def sysfs_gpus(root: Path = Path("/sys/class/drm")) -> list[Gpu]:
    """AMD and Intel GPUs from sysfs (``mem_info_vram_*`` for AMD, vendor id for the rest)."""
    gpus: list[Gpu] = []
    if not root.is_dir():
        return gpus
    for card in sorted(root.glob("card[0-9]*")):
        device = card / "device"
        try:
            vendor = _PCI_VENDORS.get((device / "vendor").read_text().strip())
        except OSError:
            continue
        if vendor not in {"amd", "intel"}:
            continue
        total = _read_mb(device / "mem_info_vram_total")
        used = _read_mb(device / "mem_info_vram_used")
        gpus.append(
            Gpu(
                vendor="amd" if vendor == "amd" else "intel",
                name=f"{vendor} {card.name}",
                vram_total_mb=total,
                vram_free_mb=total - used if total is not None and used is not None else None,
                discrete=total is not None,
                source="sysfs",
            )
        )
    return gpus


def amd_gpus() -> list[Gpu]:
    """AMD GPUs: ``rocm-smi`` when installed, else sysfs."""
    out = _run(["rocm-smi", "--showproductname", "--showmeminfo", "vram", "--json"])
    from_smi = parse_rocm_smi_json(out) if out else []
    return from_smi or [g for g in sysfs_gpus() if g.vendor == "amd"]


def apple_gpus(ram_total_mb: int | None) -> list[Gpu]:
    """Apple Silicon: one GPU sharing the unified memory."""
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return []
    brand = (_run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "Apple Silicon").strip()
    return [
        Gpu(
            vendor="apple",
            name=brand,
            vram_total_mb=ram_total_mb,
            unified_memory=True,
            discrete=False,
            source="sysctl",
        )
    ]


def vulkan_devices() -> list[str]:
    """Vulkan devices via ``vulkaninfo --summary`` (empty when the tool is missing)."""
    out = _run(["vulkaninfo", "--summary"], timeout=30.0)
    return parse_vulkaninfo_summary(out) if out else []


def cpu_info() -> CpuInfo:
    """CPU model, core count and vector features."""
    cores = os.cpu_count() or 1
    try:
        model, avx2, avx512, neon = parse_cpuinfo(Path("/proc/cpuinfo").read_text())
    except OSError:
        brand = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        model = (brand or platform.processor() or platform.machine()).strip()
        avx2 = avx512 = False
        neon = platform.machine() in {"arm64", "aarch64"}
    return CpuInfo(
        model=model or platform.machine(), cores=cores, avx2=avx2, avx512=avx512, neon=neon
    )


def memory_mb() -> tuple[int | None, int | None]:
    """``(total, available)`` RAM in MiB."""
    try:
        return parse_meminfo(Path("/proc/meminfo").read_text())
    except OSError:
        pass
    raw = (_run(["sysctl", "-n", "hw.memsize"]) or "").strip()
    return (int(raw) // (1024 * 1024) if raw.isdecimal() else None), None


def detect_hardware() -> HardwareReport:
    """Probe the machine and derive backend and profile from the free accelerator memory."""
    ram_total, ram_available = memory_mb()
    gpus = [*nvidia_gpus(), *amd_gpus(), *apple_gpus(ram_total)]
    gpus += [g for g in sysfs_gpus() if g.vendor == "intel"]
    vulkan = vulkan_devices()
    gpu = best_gpu(gpus)
    rocm = shutil.which("rocm-smi") is not None or Path("/opt/rocm").is_dir()
    backend = choose_backend(gpu, vulkan, rocm)
    vram = effective_vram_mb(gpu, ram_total)
    notes: list[str] = []
    if gpu is None and vulkan:
        notes.append("Vulkan device without a vendor tool: memory unknown, using the CPU profile")
    return HardwareReport(
        os=platform.system().lower(),
        arch=platform.machine().lower(),
        cpu=cpu_info(),
        ram_total_mb=ram_total,
        ram_available_mb=ram_available,
        gpus=gpus,
        vulkan_devices=vulkan,
        backend=backend,
        profile=choose_profile(vram, backend),
        effective_vram_mb=vram,
        notes=notes,
    )


def free_vram_mb() -> int | None:
    """Free memory of the best accelerator right now (checked before a model is loaded)."""
    return effective_vram_mb(best_gpu([*nvidia_gpus(), *amd_gpus()]), None)


def _read_mb(path: Path) -> int | None:
    try:
        return int(path.read_text().strip()) // (1024 * 1024)
    except (OSError, ValueError):
        return None
