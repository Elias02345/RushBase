import asyncio
from pathlib import Path

from rushbase_worker.assets.backends import InstalledBackend
from rushbase_worker.runtimes.lifecycle import ModelLifecycle
from rushbase_worker.runtimes.llama import (
    LlamaStartError,
    build_args,
    is_oom,
    start_with_fallback,
)
from rushbase_worker.runtimes.llama import free_port as pick_port
from rushbase_worker.runtimes.profiles import load_profiles
from rushbase_worker.runtimes.whisper import parse_language, parse_whisper_json, words_from_tokens


def test_llama_args_follow_adr_flags() -> None:
    profiles = load_profiles()
    args = build_args(Path("/x/llama-server"), profiles.variant("gpu8-4b"), profiles.common,
                      Path("/m.gguf"), Path("/mm.gguf"), 18091)  # fmt: skip
    joined = " ".join(args)
    for fragment in ("--jinja", "-np 1", "--image-min-tokens 128", "--image-max-tokens 512",
                     "-ngl 99", "-ctk q8_0", "-ctv q8_0", "-fa on", "-c 8192",
                     "--host 127.0.0.1", "--port 18091",
                     '--chat-template-kwargs {"enable_thinking":false}'):  # fmt: skip
        assert fragment in joined


def test_cpu_variant_does_not_offload() -> None:
    profiles = load_profiles()
    args = build_args(
        Path("/x"), profiles.variant("cpu-2b"), profiles.common, Path("/m"), Path("/p"), 18090
    )
    assert args[args.index("-ngl") + 1] == "0"
    assert "--no-mmproj-offload" in args


def test_port_is_in_range() -> None:
    assert 18090 <= pick_port(18090, 18099) <= 18099


def test_oom_detection() -> None:
    assert is_oom("allocating 9000 MiB failed\ncudaMalloc failed: out of memory")
    assert not is_oom("model loaded")


async def test_fallback_skips_variants_that_do_not_fit_and_records_them(tmp_path: Path) -> None:
    profiles = load_profiles()
    chain = profiles.chain("xl-9b")
    started: list[str] = []

    async def fake_start(*args: object, **_kw: object) -> object:
        variant = args[1]
        started.append(variant.name)  # type: ignore[attr-defined]
        if variant.name == "gpu8-9b":  # type: ignore[attr-defined]
            raise LlamaStartError("cudaMalloc failed: out of memory", oom=True)
        return type("S", (), {"fallbacks": [], "variant": variant})()

    import rushbase_worker.runtimes.llama as module

    original = module.start_server
    module.start_server = fake_start  # type: ignore[assignment]
    events: list[dict[str, object]] = []
    try:
        server = await start_with_fallback(
            InstalledBackend("b", tmp_path, tmp_path / "llama-server"), chain, profiles.common,
            lambda _id: (tmp_path / "m", tmp_path / "p"), lambda: 7500, tmp_path, events.append,
        )  # fmt: skip
    finally:
        module.start_server = original
    assert started == ["gpu8-9b", "gpu8-4b"]  # xl-9b skipped (needs 12500 MiB), gpu8-9b OOM
    assert server.variant.name == "gpu8-4b"
    assert server.fallbacks == ["xl-9b", "gpu8-9b"]
    assert [e["event"] for e in events] == ["variant_skipped", "variant_oom"]


async def test_lifecycle_never_holds_two_gpu_models() -> None:
    lifecycle = ModelLifecycle()
    live: list[str] = []
    peak = 0

    async def load(name: str) -> str:
        live.append(name)
        nonlocal peak
        peak = max(peak, len(live))
        return name

    async def close(name: str) -> None:
        live.remove(name)

    async def work(name: str) -> None:
        async with lifecycle.use(name, lambda: load(name), close):
            await asyncio.sleep(0.01)

    await asyncio.gather(work("vlm"), work("vlm"), work("whisper"), work("vlm"), work("whisper"))
    assert peak == 1
    await lifecycle.close()
    assert live == []
    assert lifecycle.history.count("load:vlm") >= 1


async def test_lifecycle_shares_one_resource_between_concurrent_users() -> None:
    lifecycle = ModelLifecycle()
    loads = 0

    async def load() -> str:
        nonlocal loads
        loads += 1
        return "server"

    async def close(_r: str) -> None:
        return None

    async def use() -> None:
        async with lifecycle.use("vlm", load, close):
            await asyncio.sleep(0.01)

    await asyncio.gather(use(), use(), use())
    assert loads == 1


def test_parse_language_line() -> None:
    assert parse_language("whisper_full_with_state: auto-detected language: de (p = 0.973141)") == (
        "de",
        0.973141,
    )
    assert parse_language("nothing") == (None, None)


def test_whisper_json_to_segments_and_words() -> None:
    document = {
        "result": {"language": "de"},
        "transcription": [{
            "offsets": {"from": 0, "to": 1800},
            "text": " Hallo Welt.",
            "tokens": [
                {"text": "[_BEG_]", "offsets": {"from": 0, "to": 0}, "p": 1.0},
                {"text": " Hal", "offsets": {"from": 100, "to": 400}, "p": 0.9},
                {"text": "lo", "offsets": {"from": 400, "to": 700}, "p": 0.7},
                {"text": " Welt", "offsets": {"from": 800, "to": 1500}, "p": 0.95},
                {"text": ".", "offsets": {"from": 1500, "to": 1600}, "p": 0.99},
                {"text": "[_TT_90]", "offsets": {"from": 1800, "to": 1800}, "p": 1.0},
            ],
        }],
    }  # fmt: skip
    (segment,) = parse_whisper_json(document)
    assert (segment.start_ms, segment.end_ms, segment.language) == (0, 1800, "de")
    assert [w.text for w in segment.words] == ["Hallo", "Welt."]
    assert (segment.words[0].start_ms, segment.words[0].end_ms) == (100, 700)
    assert segment.words[0].probability == 0.8
    assert words_from_tokens([]) == []
