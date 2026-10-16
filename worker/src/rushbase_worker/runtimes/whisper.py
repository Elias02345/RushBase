"""whisper.cpp CLI wrapper: transcription with word timestamps (``-ojf`` full JSON)."""

import json
import re
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rushbase_core.results_ai import TranscriptSegment, Word
from rushbase_worker.assets.backends import InstalledBackend
from rushbase_worker.runtimes.llama import library_env
from rushbase_worker.tools.process import run_tool

_LANGUAGE = re.compile(r"auto-detected language:\s*(\w+)\s*\(p\s*=\s*([0-9.]+)\)")


@dataclass(frozen=True)
class WhisperResult:
    """Parsed output of one ``whisper-cli`` run."""

    language: str | None
    language_probability: float | None
    segments: list[TranscriptSegment]
    seconds: float


def parse_language(stderr: str) -> tuple[str | None, float | None]:
    """Detected language and its probability from the CLI's log output."""
    match = _LANGUAGE.search(stderr)
    return (match.group(1), float(match.group(2))) if match else (None, None)


def _is_special(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("[_") and stripped.endswith("]")


def words_from_tokens(tokens: list[dict[str, Any]]) -> list[Word]:
    """Group sub-word tokens into words (a token with a leading space starts a new word)."""
    words: list[Word] = []
    pieces: list[tuple[str, int, int, float | None]] = []

    def flush() -> None:
        if not pieces:
            return
        text = "".join(p[0] for p in pieces).strip()
        probabilities = [p[3] for p in pieces if p[3] is not None]
        if text:
            start = pieces[0][1]
            words.append(
                Word(
                    start_ms=start,
                    end_ms=max(start, pieces[-1][2]),
                    text=text,
                    probability=min(1.0, max(0.0, sum(probabilities) / len(probabilities)))
                    if probabilities
                    else None,
                )
            )
        pieces.clear()

    for token in tokens:
        text = str(token.get("text", ""))
        if _is_special(text):
            continue
        offsets = token.get("offsets", {})
        if text.startswith(" ") and pieces:
            flush()
        pieces.append(
            (text, int(offsets.get("from", 0)), int(offsets.get("to", 0)), token.get("p"))
        )
    flush()
    return words


def parse_whisper_json(document: dict[str, Any]) -> list[TranscriptSegment]:
    """Transcript segments (with words) from whisper.cpp's full JSON output."""
    language = (document.get("result") or {}).get("language")
    segments: list[TranscriptSegment] = []
    for entry in document.get("transcription", []):
        text = str(entry.get("text", "")).strip()
        if not text:
            continue
        offsets = entry.get("offsets", {})
        start, end = int(offsets.get("from", 0)), int(offsets.get("to", 0))
        segments.append(
            TranscriptSegment(
                start_ms=start,
                end_ms=max(start, end),
                text=text,
                language=language,
                words=words_from_tokens(entry.get("tokens", [])),
            )
        )
    return segments


async def transcribe(
    backend: InstalledBackend,
    model: Path,
    audio: Path,
    *,
    use_gpu: bool,
    threads: int,
    timeout: float,
    vad_model: Path | None = None,
    language: str = "auto",
    extra_lib_dirs: Sequence[Path] = (),
) -> WhisperResult:
    """Transcribe a 16 kHz mono WAV with ``whisper-cli``; language is auto-detected by default."""
    with tempfile.TemporaryDirectory(prefix="rushbase-whisper-") as tmp:
        prefix = Path(tmp) / "out"
        args = [
            str(backend.executable), "-m", str(model), "-f", str(audio),
            "-l", language, "-ojf", "-of", str(prefix), "-t", str(threads),
        ]  # fmt: skip
        if not use_gpu:
            args.append("-ng")
        if vad_model is not None:
            args += ["--vad", "-vm", str(vad_model)]
        began = time.monotonic()
        result = await run_tool(
            "whisper-cli", args, timeout=timeout, env=library_env(backend, extra_lib_dirs)
        )
        elapsed = time.monotonic() - began
        document = json.loads(prefix.with_suffix(".json").read_text(encoding="utf-8"))
    code, probability = parse_language(result.stderr)
    language_found = code or (document.get("result") or {}).get("language")
    return WhisperResult(language_found, probability, parse_whisper_json(document), elapsed)
