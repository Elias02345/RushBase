"""``llama-server`` subprocess manager (vision-language model, OpenAI-compatible HTTP API)."""

import asyncio
import contextlib
import os
import socket
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from rushbase_worker.assets.backends import InstalledBackend
from rushbase_worker.runtimes.profiles import Common, Variant

OOM_MARKERS = (
    "out of memory",
    "cudamalloc failed",
    "failed to allocate",
    "unable to allocate",
    "cuda error: out of memory",
    "vk::device::allocatememory",
    "errordeviceoutofmemory",
)
STOP_GRACE_SECONDS = 15.0


class LlamaStartError(Exception):
    """``llama-server`` did not become healthy. ``oom`` marks out-of-memory failures."""

    def __init__(self, message: str, *, oom: bool = False) -> None:
        super().__init__(message)
        self.oom = oom


@dataclass(frozen=True)
class ChatResult:
    """One completion with the numbers the benchmark and the retry logic need."""

    content: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    seconds: float
    tokens_per_second: float | None


@dataclass
class LlamaServer:
    """A running ``llama-server`` bound to 127.0.0.1."""

    variant: Variant
    port: int
    process: asyncio.subprocess.Process
    log_path: Path
    started_seconds: float
    fallbacks: list[str] = field(default_factory=list)
    """Variants that were skipped or failed before this one (recorded as analysis quality)."""
    _client: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def pid(self) -> int:
        return self.process.pid

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(600.0))
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        schema: dict[str, Any] | None = None,
        schema_name: str = "result",
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> ChatResult:
        """Run a chat completion; ``schema`` constrains the output (converted to a grammar)."""
        body: dict[str, Any] = {
            "model": self.variant.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }
        began = time.monotonic()
        response = await self._http().post("/v1/chat/completions", json=body)
        response.raise_for_status()
        payload = response.json()
        choice = payload["choices"][0]
        usage = payload.get("usage", {})
        timings = payload.get("timings", {})
        return ChatResult(
            content=choice["message"].get("content") or "",
            finish_reason=choice.get("finish_reason") or "",
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            seconds=time.monotonic() - began,
            tokens_per_second=timings.get("predicted_per_second"),
        )

    async def stop(self) -> None:
        """Stop the server gracefully (SIGTERM, then SIGKILL after a grace period)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self.process.returncode is not None:
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), STOP_GRACE_SECONDS)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()


def free_port(low: int, high: int) -> int:
    """First bindable port in ``low..high`` on 127.0.0.1."""
    for port in range(low, high + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise LlamaStartError(f"no free port in {low}-{high}")


def build_args(
    executable: Path, variant: Variant, common: Common, model: Path, mmproj: Path, port: int
) -> list[str]:
    """The ``llama-server`` command line for ``variant`` (ADR-0006 flags + profile specifics)."""
    args = [
        str(executable),
        "-m", str(model),
        "--mmproj", str(mmproj),
        "--host", common.host,
        "--port", str(port),
        "-c", str(variant.ctx),
        "-ngl", str(variant.ngl),
        "-ctk", variant.kv_quant,
        "-ctv", variant.kv_quant,
        "--image-max-tokens", str(variant.image_max_tokens),
        *common.args,
    ]  # fmt: skip
    if variant.ngl == 0:
        args += ["--no-mmproj-offload"]
    if variant.chat_template_kwargs:
        args += ["--chat-template-kwargs", variant.chat_template_kwargs]
    return args


def library_env(backend: InstalledBackend, extra: Sequence[Path] = ()) -> dict[str, str]:
    """Environment with the backend's shared libraries (and ``extra`` dirs) on the loader path."""
    paths = [str(p) for p in [*backend.lib_dirs, *extra]]
    existing = os.environ.get("LD_LIBRARY_PATH")
    if existing:
        paths.append(existing)
    return {**os.environ, "LD_LIBRARY_PATH": ":".join(paths)}


async def start_server(
    backend: InstalledBackend,
    variant: Variant,
    common: Common,
    model: Path,
    mmproj: Path,
    log_dir: Path,
) -> LlamaServer:
    """Start ``llama-server`` for ``variant`` and wait until it answers ``/health``."""
    port = free_port(common.port_min, common.port_max)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"llama-server-{port}.log"
    args = build_args(backend.executable, variant, common, model, mmproj, port)
    with log_path.open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
            env=library_env(backend),
        )
    began = time.monotonic()
    server = LlamaServer(variant, port, process, log_path, began)
    try:
        await _wait_healthy(server, common.health_timeout_seconds)
    except BaseException:
        await server.stop()
        raise
    server.started_seconds = time.monotonic() - began
    return server


async def _wait_healthy(server: LlamaServer, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=5.0) as client:
        while time.monotonic() < deadline:
            if server.process.returncode is not None:
                tail = log_tail(server.log_path)
                raise LlamaStartError(
                    f"llama-server exited with status {server.process.returncode}: {tail[-600:]}",
                    oom=is_oom(tail),
                )
            with contextlib.suppress(httpx.HTTPError):
                if (await client.get(f"{server.base_url}/health")).status_code == httpx.codes.OK:
                    return
            await asyncio.sleep(1.0)
    tail = log_tail(server.log_path)
    raise LlamaStartError(f"llama-server not healthy after {timeout:.0f}s: {tail[-400:]}",
                          oom=is_oom(tail))  # fmt: skip


def log_tail(path: Path, size: int = 20000) -> str:
    """The end of a server log."""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-size:].decode("utf-8", errors="replace")


def is_oom(text: str) -> bool:
    """Whether a log shows an out-of-memory failure."""
    lowered = text.lower()
    return any(marker in lowered for marker in OOM_MARKERS)


async def start_with_fallback(
    backend: InstalledBackend,
    chain: list[Variant],
    common: Common,
    resolve: Callable[[str], tuple[Path, Path]],
    free_vram_mb: Callable[[], int | None],
    log_dir: Path,
    on_event: Callable[[dict[str, object]], None] = lambda _event: None,
) -> LlamaServer:
    """Start the first variant of ``chain`` that fits; record the ones that did not.

    A variant is skipped without trying when its ``min_free_mb`` exceeds the free memory; an
    out-of-memory failure during startup moves on to the next smaller variant (ADR-0006).
    """
    skipped: list[str] = []
    last: LlamaStartError | None = None
    for variant in chain:
        free = free_vram_mb() if variant.ngl > 0 else None
        if free is not None and variant.min_free_mb > free:
            skipped.append(variant.name)
            on_event({"event": "variant_skipped", "variant": variant.name,
                      "reason": f"needs {variant.min_free_mb} MiB, {free} MiB free"})  # fmt: skip
            continue
        model, mmproj = resolve(variant.model)
        try:
            server = await start_server(backend, variant, common, model, mmproj, log_dir)
        except LlamaStartError as exc:
            if not exc.oom:
                raise
            last = exc
            skipped.append(variant.name)
            on_event({"event": "variant_oom", "variant": variant.name, "error": str(exc)[:300]})
            continue
        server.fallbacks = skipped
        return server
    raise last or LlamaStartError("no VLM variant fits into the available memory", oom=True)
