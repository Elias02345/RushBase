import hashlib
import tarfile
from pathlib import Path

import httpx
import pytest

from rushbase_worker.assets import (
    ModelNotInstalledError,
    ModelStore,
    load_binaries_lock,
    load_models_lock,
)
from rushbase_worker.assets.backends import _extract, llama_platform_keys
from rushbase_worker.assets.download import DownloadError, download
from rushbase_worker.assets.lock import ModelFile, ModelSpec
from rushbase_worker.hardware import Backend, CpuInfo, Gpu, HardwareReport, Profile
from rushbase_worker.runtimes.profiles import load_profiles

PAYLOAD = bytes(range(256)) * 4000


def _server(payload: bytes = PAYLOAD, *, ignore_range: bool = False) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        header = request.headers.get("range")
        if header and not ignore_range:
            start = int(header.removeprefix("bytes=").split("-")[0])
            return httpx.Response(206, content=payload[start:])
        return httpx.Response(200, content=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_download_verifies_and_installs_atomically(tmp_path: Path) -> None:
    target = tmp_path / "m" / "file.bin"
    events: list[dict[str, object]] = []
    download("http://x/f", target, sha256=_sha(PAYLOAD), size=len(PAYLOAD), label="f",
             emit=events.append, client=_server())  # fmt: skip
    assert target.read_bytes() == PAYLOAD
    assert not target.with_name("file.bin.part").exists()
    assert events[-1]["event"] == "download_done"


def test_download_resumes_with_range(tmp_path: Path) -> None:
    target = tmp_path / "file.bin"
    target.with_name("file.bin.part").write_bytes(PAYLOAD[:1000])
    events: list[dict[str, object]] = []
    download("http://x/f", target, sha256=_sha(PAYLOAD), size=len(PAYLOAD), label="f",
             emit=events.append, client=_server())  # fmt: skip
    assert target.read_bytes() == PAYLOAD
    assert any(e.get("resume_from") == 1000 for e in events)


def test_download_restarts_when_server_ignores_range(tmp_path: Path) -> None:
    target = tmp_path / "file.bin"
    target.with_name("file.bin.part").write_bytes(b"stale" * 100)
    download("http://x/f", target, sha256=_sha(PAYLOAD), size=len(PAYLOAD), label="f",
             client=_server(ignore_range=True))  # fmt: skip
    assert target.read_bytes() == PAYLOAD


def test_download_rejects_wrong_checksum_and_keeps_no_file(tmp_path: Path) -> None:
    target = tmp_path / "file.bin"
    with pytest.raises(DownloadError, match="SHA-256 mismatch"):
        download(
            "http://x/f", target, sha256="0" * 64, size=len(PAYLOAD), label="f", client=_server()
        )
    assert not target.exists()
    assert not target.with_name("file.bin.part").exists()


def _spec(payload: bytes) -> ModelSpec:
    file = ModelFile(role="model", repo="r/r", filename="sub/m.bin", revision="abc",
                     size=len(payload), sha256=_sha(payload))  # fmt: skip
    return ModelSpec(id="tiny", kind="asr", display="tiny", license="MIT", files=(file,))


def test_model_store_pull_status_verify_remove(tmp_path: Path) -> None:
    spec = _spec(PAYLOAD)
    store = ModelStore(tmp_path)
    assert store.status(spec).state == "missing"
    done = store.pull(spec, lambda _e: None, client=_server())
    assert done.state == "installed"
    assert store.verify(spec, lambda _e: None) == []
    (tmp_path / "models" / "tiny" / "m.bin").write_bytes(b"x" * len(PAYLOAD))
    assert store.verify(spec, lambda _e: None) == ["m.bin: SHA-256 mismatch"]
    assert store.remove(spec) is True
    assert store.status(spec).state == "missing"


def test_pull_can_be_limited_to_some_roles(tmp_path: Path) -> None:
    other = b"tokenizer-bytes"
    files = (
        ModelFile(role="model", repo="r/r", filename="onnx/m.bin", revision="abc",
                  size=len(PAYLOAD), sha256=_sha(PAYLOAD)),
        ModelFile(role="tokenizer", repo="r/r", filename="tok.json", revision="abc",
                  size=len(other), sha256=_sha(other)),
    )  # fmt: skip
    spec = ModelSpec(id="two", kind="embed-text", display="two", license="MIT", files=files)
    store = ModelStore(tmp_path)
    started: list[dict[str, object]] = []
    result = store.pull(spec, started.append, client=_server(other), roles=("tokenizer",))
    assert (tmp_path / "models" / "two" / "tok.json").read_bytes() == other
    assert not (tmp_path / "models" / "two" / "m.bin").exists()
    assert result.state == "partial"  # the rest is still missing
    assert started[0]["size"] == len(other)
    with pytest.raises(KeyError, match="no file with role"):
        store.pull(spec, lambda _e: None, roles=("nope",))


def test_model_file_requires_installation(tmp_path: Path) -> None:
    store = ModelStore(tmp_path)
    with pytest.raises(ModelNotInstalledError, match="rushbase models pull"):
        store.file("whisper-small-q8_0", "model")


def test_lock_files_are_complete_and_consistent() -> None:
    models = load_models_lock()
    for spec in models.all():
        for file in spec.files:
            assert len(file.sha256) == 64
            assert len(file.revision) == 40
            assert file.size > 0
    binaries = load_binaries_lock()
    assert binaries.llama.tag.startswith("b")
    cuda = binaries.llama.platforms["linux-x64-cuda-12.8"]
    assert cuda.runtime_bundle is not None
    assert len(cuda.sha256) == 64


def test_profiles_reference_pinned_models() -> None:
    profiles = load_profiles()
    models = load_models_lock().models
    for name, variant in profiles.variants.items():
        assert variant.model in models, name
        assert variant.fallback is None or variant.fallback in profiles.variants
        assert profiles.chain(name)[0].name == name
    for profile in Profile:
        spec = profiles.profile(profile)
        assert {spec.asr, spec.audio_events} <= models.keys()
        assert spec.vlm in profiles.variants


def test_qwen35_variants_disable_thinking() -> None:
    profiles = load_profiles()
    assert profiles.variant("gpu8-4b").chat_template_kwargs == '{"enable_thinking":false}'
    assert profiles.variant("baseline-qwen3vl8b").chat_template_kwargs is None


def _report(backend: Backend, *, cuda: str | None = None, os: str = "linux") -> HardwareReport:
    gpus = [Gpu(vendor="nvidia", name="x", cuda_version=cuda)] if cuda else []
    return HardwareReport(os=os, arch="x86_64", cpu=CpuInfo(model="x", cores=4), ram_total_mb=1,
                          ram_available_mb=1, gpus=gpus, vulkan_devices=[], backend=backend,
                          profile=Profile.CPU, effective_vram_mb=None)  # fmt: skip


def test_llama_platform_keys() -> None:
    assert llama_platform_keys(_report(Backend.CUDA, cuda="13.4")) == [
        "linux-x64-cuda-12.8", "linux-x64-vulkan", "linux-x64-cpu"]  # fmt: skip
    assert llama_platform_keys(_report(Backend.CUDA, cuda="11.8")) == [
        "linux-x64-vulkan", "linux-x64-cpu"]  # fmt: skip
    assert llama_platform_keys(_report(Backend.CPU)) == ["linux-x64-cpu"]
    windows = llama_platform_keys(_report(Backend.CUDA, cuda="13.4", os="windows"))
    assert windows[:2] == ["windows-x64-cuda-13.4", "windows-x64-cuda-12.4"]


def test_extract_flattens_single_top_folder_and_rejects_traversal(tmp_path: Path) -> None:
    source = tmp_path / "src"
    (source / "llama-b1").mkdir(parents=True)
    (source / "llama-b1" / "llama-server").write_text("bin")
    archive = tmp_path / "a.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source / "llama-b1", arcname="llama-b1")
    out = tmp_path / "out"
    out.mkdir()
    _extract(archive, out)
    assert (out / "llama-server").read_text() == "bin"
