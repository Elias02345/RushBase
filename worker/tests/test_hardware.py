import json

from rushbase_worker.hardware import Backend, Gpu, Profile, choose_backend, choose_profile
from rushbase_worker.hardware.parse import (
    parse_cpuinfo,
    parse_cuda_version,
    parse_meminfo,
    parse_nvidia_smi_csv,
    parse_rocm_smi_json,
    parse_vulkaninfo_summary,
)
from rushbase_worker.hardware.profile import effective_vram_mb, smaller_profile

NVIDIA_CSV = "NVIDIA GeForce RTX 3070, 8192, 7275, 615.71.09, 8.6\n"
NVIDIA_HEADER = "| NVIDIA-SMI 615.71.09   KMD Version: 615.71.09   CUDA UMD Version: 13.4  |"


def test_parse_nvidia_smi() -> None:
    gpus = parse_nvidia_smi_csv(NVIDIA_CSV, parse_cuda_version(NVIDIA_HEADER))
    assert len(gpus) == 1
    gpu = gpus[0]
    assert (gpu.name, gpu.vram_total_mb, gpu.vram_free_mb) == (
        "NVIDIA GeForce RTX 3070",
        8192,
        7275,
    )
    assert gpu.cuda_version == "13.4"
    assert gpu.compute_capability == "8.6"


def test_parse_nvidia_smi_ignores_garbage() -> None:
    assert parse_nvidia_smi_csv("No devices were found\n") == []


def test_parse_rocm_smi() -> None:
    document = {
        "card0": {
            "Card Series": "Radeon RX 7800 XT",
            "VRAM Total Memory (B)": str(16 * 1024**3),
            "VRAM Total Used Memory (B)": str(1 * 1024**3),
        }
    }
    (gpu,) = parse_rocm_smi_json(json.dumps(document))
    assert gpu.vendor == "amd"
    assert (gpu.vram_total_mb, gpu.vram_free_mb) == (16384, 15360)
    assert parse_rocm_smi_json("not json") == []


def test_parse_vulkaninfo_drops_software_rasterizers() -> None:
    text = (
        "GPU0:\n\tdeviceName = NVIDIA GeForce RTX 3070\nGPU1:\n\tdeviceName = llvmpipe (LLVM 19)\n"
    )
    assert parse_vulkaninfo_summary(text) == ["NVIDIA GeForce RTX 3070"]


def test_parse_proc_files() -> None:
    assert parse_meminfo("MemTotal:       32752000 kB\nMemAvailable:   20000000 kB\n") == (
        31984,
        19531,
    )
    model, avx2, avx512, neon = parse_cpuinfo(
        "model name\t: Test CPU\nflags\t\t: fpu avx2 avx512f\n"
    )
    assert (model, avx2, avx512, neon) == ("Test CPU", True, True, False)


def test_profile_tiers() -> None:
    assert choose_profile(24_000, Backend.CUDA) is Profile.XL
    assert choose_profile(7_275, Backend.CUDA) is Profile.GPU8
    assert choose_profile(5_000, Backend.VULKAN) is Profile.GPU4
    assert choose_profile(2_000, Backend.CUDA) is Profile.CPU
    assert choose_profile(None, Backend.CPU) is Profile.CPU
    assert choose_profile(24_000, Backend.CPU) is Profile.CPU


def test_backend_choice() -> None:
    nvidia = Gpu(vendor="nvidia", name="x")
    amd = Gpu(vendor="amd", name="x")
    assert choose_backend(nvidia, [], False) is Backend.CUDA
    assert choose_backend(amd, ["x"], True) is Backend.ROCM
    assert choose_backend(amd, ["x"], False) is Backend.VULKAN
    assert choose_backend(Gpu(vendor="intel", name="Arc"), ["Arc"], False) is Backend.VULKAN
    assert choose_backend(Gpu(vendor="apple", name="M2"), [], False) is Backend.METAL
    assert choose_backend(None, ["some gpu"], False) is Backend.VULKAN
    assert choose_backend(None, [], False) is Backend.CPU


def test_apple_uses_share_of_unified_memory() -> None:
    apple = Gpu(vendor="apple", name="M2", unified_memory=True, discrete=False)
    assert effective_vram_mb(apple, 16_384) == 9_830


def test_oom_fallback_order() -> None:
    assert smaller_profile(Profile.XL) is Profile.GPU8
    assert smaller_profile(Profile.GPU4) is Profile.CPU
    assert smaller_profile(Profile.CPU) is None
