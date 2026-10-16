"""Pure parsers for the output of vendor tools (testable without the hardware)."""

import contextlib
import json
import re
from typing import Any

from rushbase_worker.hardware.model import Gpu

_VK_DEVICE = re.compile(r"^\s*deviceName\s*=\s*(.+?)\s*$", re.MULTILINE)
_CUDA_HEADER = re.compile(r"CUDA (?:UMD )?Version:\s*([0-9.]+)")


def parse_nvidia_smi_csv(text: str, cuda_version: str | None = None) -> list[Gpu]:
    """Parse ``nvidia-smi --query-gpu=name,memory.total,memory.free,driver_version,compute_cap``.

    Rows are CSV without header and units (``--format=csv,noheader,nounits``).
    """
    gpus: list[Gpu] = []
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        name, total, free, driver, capability = parts[:5]
        gpus.append(
            Gpu(
                vendor="nvidia",
                name=name,
                vram_total_mb=_int(total),
                vram_free_mb=_int(free),
                driver=driver,
                cuda_version=cuda_version,
                compute_capability=capability if capability.replace(".", "").isdecimal() else None,
                source="nvidia-smi",
            )
        )
    return gpus


def parse_cuda_version(nvidia_smi_header: str) -> str | None:
    """Highest CUDA version the driver supports, from the plain ``nvidia-smi`` header."""
    match = _CUDA_HEADER.search(nvidia_smi_header)
    return match.group(1) if match else None


def parse_rocm_smi_json(text: str) -> list[Gpu]:
    """Parse ``rocm-smi --showproductname --showmeminfo vram --json``."""
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    gpus: list[Gpu] = []
    for key, entry in sorted(data.items()):
        if not key.startswith("card") or not isinstance(entry, dict):
            continue
        total = _bytes_to_mb(entry.get("VRAM Total Memory (B)"))
        used = _bytes_to_mb(entry.get("VRAM Total Used Memory (B)"))
        gpus.append(
            Gpu(
                vendor="amd",
                name=str(entry.get("Card Series") or entry.get("Card series") or key),
                vram_total_mb=total,
                vram_free_mb=total - used if total is not None and used is not None else None,
                source="rocm-smi",
            )
        )
    return gpus


def parse_vulkaninfo_summary(text: str) -> list[str]:
    """Device names from ``vulkaninfo --summary``; software rasterizers (llvmpipe) are dropped."""
    names = [m.group(1) for m in _VK_DEVICE.finditer(text)]
    return [n for n in names if "llvmpipe" not in n.lower() and "swiftshader" not in n.lower()]


def parse_meminfo(text: str) -> tuple[int | None, int | None]:
    """``(total_mb, available_mb)`` from ``/proc/meminfo``."""
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in {"MemTotal", "MemAvailable"}:
            with contextlib.suppress(ValueError, IndexError):
                values[key] = int(rest.split()[0]) // 1024
    return values.get("MemTotal"), values.get("MemAvailable")


def parse_cpuinfo(text: str) -> tuple[str, bool, bool, bool]:
    """``(model, avx2, avx512, neon)`` from ``/proc/cpuinfo``."""
    model = ""
    flags: set[str] = set()
    for line in text.splitlines():
        key, _, value = line.partition(":")
        key = key.strip()
        if key == "model name" and not model:
            model = value.strip()
        elif key in {"flags", "Features"} and not flags:
            flags = set(value.split())
    return (
        model,
        "avx2" in flags,
        any(f.startswith("avx512") for f in flags),
        "asimd" in flags or "neon" in flags,
    )


def _int(value: str) -> int | None:
    try:
        return int(float(value))
    except ValueError:
        return None


def _bytes_to_mb(value: Any) -> int | None:
    try:
        return int(value) // (1024 * 1024)
    except (TypeError, ValueError):
        return None
