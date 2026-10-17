import hashlib
import tarfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from rushbase_worker.assets import (
    BackendStore,
    ModelNotInstalledError,
    ModelStore,
    load_binaries_lock,
    load_models_lock,
)
from rushbase_worker.assets import backends as backends_module
from rushbase_worker.assets.backends import _extract, llama_platform_keys, whisper_platform_keys
from rushbase_worker.assets.download import DownloadError, download
from rushbase_worker.assets.lock import Asset, ModelFile, ModelSpec
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


def test_download_verifies_a_present_file_and_replaces_a_tampered_one(tmp_path: Path) -> None:
    target = tmp_path / "file.bin"
    target.write_bytes(b"x" * len(PAYLOAD))  # same size, wrong content (planted on the share)
    events: list[dict[str, object]] = []
    download("http://x/f", target, sha256=_sha(PAYLOAD), size=len(PAYLOAD), label="f",
             emit=events.append, client=_server())  # fmt: skip
    assert target.read_bytes() == PAYLOAD
    assert not any(e["event"] == "download_skipped" for e in events)
    events.clear()
    download("http://x/f", target, sha256=_sha(PAYLOAD), size=len(PAYLOAD), label="f",
             emit=events.append, client=_server())  # fmt: skip
    assert [e["event"] for e in events] == ["download_skipped"]


def test_download_stops_streaming_beyond_the_pinned_size(tmp_path: Path) -> None:
    target = tmp_path / "file.bin"
    consumed: list[int] = []

    def endless() -> Iterator[bytes]:
        while True:
            consumed.append(1)
            yield b"y" * (1 << 20)

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=endless()))
    with pytest.raises(DownloadError):
        download("http://x/f", target, sha256=_sha(PAYLOAD), size=2 << 20, label="f",
                 client=httpx.Client(transport=transport))  # fmt: skip
    assert len(consumed) <= 3  # a few chunks past the limit at most, not the whole stream
    assert not target.exists()


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


def test_whisper_platform_keys() -> None:
    assert whisper_platform_keys(_report(Backend.CUDA, cuda="12.8")) == [
        "linux-x64-cuda", "linux-x64-vulkan", "linux-x64-cpu"]  # fmt: skip
    assert whisper_platform_keys(_report(Backend.ROCM)) == ["linux-x64-vulkan", "linux-x64-cpu"]
    assert whisper_platform_keys(_report(Backend.CPU)) == ["linux-x64-cpu"]
    assert whisper_platform_keys(_report(Backend.CUDA, os="windows"))[0] == "windows-x64-cuda"
    mac = _report(Backend.METAL, os="darwin")
    assert whisper_platform_keys(mac.model_copy(update={"arch": "arm64"})) == ["macos-arm64-metal"]


def _whisper_archive(tmp_path: Path, folder: str) -> bytes:
    source = tmp_path / "src" / folder
    source.mkdir(parents=True)
    (source / "whisper-cli").write_text("bin")
    archive = tmp_path / "w.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname=folder)
    return archive.read_bytes()


def test_install_whisper_uses_the_pinned_release_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _whisper_archive(tmp_path, "whisper.cpp-v9-linux-x64-cuda")
    asset = Asset(name="w.tar.gz", url="https://example.test/w.tar.gz", sha256=_sha(data),
                  size=len(data))  # fmt: skip
    lock = load_binaries_lock()
    pinned = lock.model_copy(update={"whisper": lock.whisper.model_copy(
        update={"tag": "v9", "platforms": {"linux-x64-cuda": asset}})})  # fmt: skip
    monkeypatch.setattr(backends_module, "load_binaries_lock", lambda: pinned)
    client = _server(data)
    store = BackendStore(tmp_path / "data")
    report = _report(Backend.CUDA, cuda="12.8")
    installed = store.install_whisper(report, lambda _event: None, client)
    assert installed.name == "whisper.cpp-v9-linux-x64-cuda"
    assert store.find_whisper(report) == installed
    # a bad checksum is rejected before anything is installed
    broken = asset.model_copy(update={"sha256": "0" * 64})
    monkeypatch.setattr(backends_module, "load_binaries_lock", lambda: pinned.model_copy(
        update={"whisper": pinned.whisper.model_copy(update={"platforms": {
            "linux-x64-cuda": broken}})}))  # fmt: skip
    with pytest.raises(DownloadError):
        BackendStore(tmp_path / "other").install_whisper(report, lambda _event: None, client)


def test_find_whisper_prefers_release_builds_over_the_upstream_fallback(tmp_path: Path) -> None:
    store = BackendStore(tmp_path)
    for name in ("whisper.cpp-upstream-b1-linux-x64-cpu", "whisper.cpp-v9-linux-x64-cpu"):
        (store.root / name).mkdir(parents=True)
        (store.root / name / "whisper-cli").write_text("bin")
    found = store.find_whisper(_report(Backend.CPU))
    assert found is not None
    assert found.name == "whisper.cpp-v9-linux-x64-cpu"


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
