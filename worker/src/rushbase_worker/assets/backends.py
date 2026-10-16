"""Inference binaries (llama.cpp, whisper.cpp): selection, install and lookup in the data dir."""

import json
import shutil
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from rushbase_worker.assets.download import ProgressSink, download
from rushbase_worker.assets.lock import Asset, load_binaries_lock
from rushbase_worker.hardware.model import Backend, HardwareReport

EXE = ".exe" if sys.platform == "win32" else ""


def _driver_cuda(report: HardwareReport) -> tuple[int, int]:
    for gpu in report.gpus:
        if gpu.vendor == "nvidia" and gpu.cuda_version:
            major, _, minor = gpu.cuda_version.partition(".")
            return int(major), int(minor or 0)
    return (0, 0)


def platform_prefix(report: HardwareReport) -> str:
    """``linux-x64``, ``macos-arm64`` or ``windows-x64`` (empty when unsupported)."""
    arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(report.arch)
    system = {"linux": "linux", "darwin": "macos", "windows": "windows"}.get(report.os)
    return f"{system}-{arch}" if system and arch else ""


def llama_platform_keys(report: HardwareReport) -> list[str]:
    """Lock-file platform keys for this machine, most preferred first (ADR-0008)."""
    prefix = platform_prefix(report)
    if prefix == "macos-arm64":
        return ["macos-arm64-metal"]
    if prefix not in {"linux-x64", "windows-x64"}:
        return []
    keys: list[str] = []
    if report.backend is Backend.CUDA:
        driver = _driver_cuda(report)
        if prefix == "linux-x64" and driver >= (12, 8):
            keys.append("linux-x64-cuda-12.8")
        if prefix == "windows-x64":
            keys += [
                k
                for k, need in (
                    ("windows-x64-cuda-13.4", (13, 4)),
                    ("windows-x64-cuda-12.4", (12, 4)),
                )
                if driver >= need
            ]
    if report.backend is Backend.ROCM and prefix == "linux-x64":
        keys.append("linux-x64-rocm")
    if report.backend in {Backend.CUDA, Backend.ROCM, Backend.VULKAN, Backend.SYCL}:
        keys.append(f"{prefix}-vulkan")
    keys.append(f"{prefix}-cpu")
    return list(dict.fromkeys(keys))


@dataclass(frozen=True)
class InstalledBackend:
    """A backend directory in the data dir."""

    name: str
    path: Path
    executable: Path

    @property
    def lib_dirs(self) -> list[Path]:
        """Directories holding shared libraries the executable needs."""
        return [self.executable.parent, self.path]


def _find(root: Path, name: str) -> Path | None:
    for candidate in sorted(root.rglob(name + EXE)):
        if candidate.is_file():
            return candidate
    return None


class BackendStore:
    """``<data_dir>/backends``: installed llama.cpp and whisper.cpp builds."""

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "backends"

    def llama_dir(self, key: str) -> Path:
        return self.root / f"llama.cpp-{load_binaries_lock().llama.tag}-{key}"

    def find_llama(self, keys: list[str]) -> InstalledBackend | None:
        """The first installed llama.cpp build among ``keys`` (preference order)."""
        for key in keys:
            directory = self.llama_dir(key)
            executable = _find(directory, "llama-server") if directory.is_dir() else None
            if executable is not None and (directory / ".installed").is_file():
                return InstalledBackend(directory.name, directory, executable)
        return None

    def find_whisper(self, report: HardwareReport) -> InstalledBackend | None:
        """A whisper.cpp build for the detected backend (local/CI build), else the CPU fallback."""
        if not self.root.is_dir():
            return None
        prefix = platform_prefix(report)
        wanted = [f"whisper.cpp-*-{prefix}-{report.backend.value}"]
        if report.backend not in {Backend.CPU, Backend.VULKAN}:
            wanted.append(f"whisper.cpp-*-{prefix}-vulkan")
        wanted.append(f"whisper.cpp-*-{prefix}-cpu")
        for pattern in wanted:
            for directory in sorted(self.root.glob(pattern)):
                executable = _find(directory, "whisper-cli")
                if executable is not None:
                    return InstalledBackend(directory.name, directory, executable)
        return None

    def install_llama(
        self, report: HardwareReport, emit: ProgressSink, client: httpx.Client | None = None
    ) -> InstalledBackend:
        """Download, verify and unpack the llama.cpp build matching ``report``."""
        lock = load_binaries_lock().llama
        keys = [k for k in llama_platform_keys(report) if k in lock.platforms]
        if not keys:
            raise RuntimeError(f"no llama.cpp build is pinned for {report.os}/{report.arch}")
        present = self.find_llama(keys)
        if present is not None:
            emit({"event": "backend_present", "name": present.name})
            return present
        key = keys[0]
        platform = lock.platforms[key]
        target = self.llama_dir(key)
        self._install_archives(target, [platform, platform.runtime_bundle], emit, client)
        installed = self.find_llama([key])
        if installed is None:
            raise RuntimeError(f"{target} contains no llama-server")
        return installed

    def install_whisper_fallback(
        self, report: HardwareReport, emit: ProgressSink, client: httpx.Client | None = None
    ) -> InstalledBackend:
        """Install the upstream CPU build of whisper.cpp (no GPU builds are published upstream)."""
        fallback = load_binaries_lock().whisper.upstream_cpu_fallback
        if fallback is None or platform_prefix(report) != "linux-x64":
            raise RuntimeError("no upstream whisper.cpp binary for this platform")
        target = self.root / f"whisper.cpp-upstream-{fallback.release}-linux-x64-cpu"
        if not target.is_dir():
            self._install_archives(target, [fallback], emit, client)
        executable = _find(target, "whisper-cli") or _find(target, "main")
        if executable is None:
            raise RuntimeError(f"{target} contains no whisper-cli")
        return InstalledBackend(target.name, target, executable)

    def _install_archives(
        self,
        target: Path,
        archives: list[Asset | None],
        emit: ProgressSink,
        client: httpx.Client | None,
    ) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.root))
        try:
            for asset in archives:
                if asset is None:
                    continue
                archive = self.root / ".downloads" / asset.name
                download(asset.url, archive, sha256=asset.sha256, size=asset.size,
                         label=asset.name, emit=emit, client=client)  # fmt: skip
                _extract(archive, staging)
                archive.unlink()
            (staging / ".installed").write_text(json.dumps([a.name for a in archives if a]))
            if target.exists():
                shutil.rmtree(target)
            staging.rename(target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        emit({"event": "backend_installed", "name": target.name})

    def installed(self) -> list[str]:
        """Names of the backend directories present."""
        if not self.root.is_dir():
            return []
        return sorted(
            p.name for p in self.root.iterdir() if p.is_dir() and not p.name.startswith(".")
        )


def _extract(archive: Path, destination: Path) -> None:
    """Unpack ``archive`` into ``destination``; one common top-level folder is flattened."""
    with tempfile.TemporaryDirectory(dir=destination) as tmp:
        scratch = Path(tmp)
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zipped:
                for member in zipped.infolist():
                    resolved = (scratch / member.filename).resolve()
                    if not resolved.is_relative_to(scratch.resolve()):
                        raise RuntimeError(f"unsafe path in archive: {member.filename}")
                zipped.extractall(scratch)  # noqa: S202 - member paths checked above
        else:
            with tarfile.open(archive) as tarred:
                tarred.extractall(scratch, filter="data")
        entries = list(scratch.iterdir())
        root = entries[0] if len(entries) == 1 and entries[0].is_dir() else scratch
        for item in root.iterdir():
            destination_item = destination / item.name
            if destination_item.exists() and destination_item.is_dir():
                shutil.copytree(item, destination_item, symlinks=True, dirs_exist_ok=True)
            else:
                shutil.move(item, destination_item)
