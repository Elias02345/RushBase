"""Prompting and structured calls of the vision stage."""

import base64
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rushbase_core.results_ai import AssetSummary, VisualDescription
from rushbase_worker.runtimes.llama import ChatResult, LlamaServer
from rushbase_worker.taxonomy import TaxonomySnapshot
from rushbase_worker.vision.schema import (
    SchemaError,
    build_schema,
    parse_description,
    parse_summary,
    summary_schema,
    to_visual_description,
)

PROMPT_VERSION = "v1"
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
TEMPERATURES = (0.2, 0.0)
"""First attempt, then one retry at temperature 0 (ADR-0006 decision 4)."""


class VisionError(Exception):
    """The model did not produce a valid answer, also after the retry."""


@dataclass(frozen=True)
class Frame:
    """One image sent to the model."""

    path: Path
    t_ms: int | None = None
    label: str = ""


@dataclass
class CallStats:
    """Numbers of one structured call (all attempts), used by the benchmark."""

    attempts: int = 0
    valid: bool = False
    seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_per_second: float | None = None
    errors: list[str] = field(default_factory=list)


def load_prompt(name: str) -> str:
    """A versioned prompt file (``prompts/v1_<name>.md``)."""
    return (PROMPT_DIR / f"{PROMPT_VERSION}_{name}.md").read_text(encoding="utf-8")


def render(template: str, **values: str) -> str:
    """Replace ``{{name}}`` placeholders (JSON braces in prompts stay untouched)."""
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def taxonomy_section(taxonomy: TaxonomySnapshot) -> str:
    """Prompt text for the configured taxonomy fields."""
    if not taxonomy.fields:
        return ""
    lines = [
        "",
        "Taxonomy fields (`tax_<key>`): pick a value only if it clearly applies, otherwise "
        "leave the array empty:",
    ]
    for field_ in taxonomy.fields:
        values = ", ".join(
            (f"{v.value} ({v.description})" if v.description else v.value) for v in field_.values
        )
        lines.append(f"- tax_{field_.key} ({field_.label_en}, {field_.kind}): {values}")
    return "\n".join(lines) + "\n"


def flags_section(taxonomy: TaxonomySnapshot) -> str:
    """Prompt text for the review flags."""
    if not taxonomy.flags:
        return ""
    lines = ["", "Check flags (`flag_<key>`): answer true/false with a short reason:"]
    lines += [f"- flag_{f.key}: {f.description_en}" for f in taxonomy.flags]
    return "\n".join(lines) + "\n"


def _clock(ms: int) -> str:
    seconds, millis = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{seconds:02d}.{millis // 100}"


def image_part(path: Path) -> dict[str, Any]:
    """OpenAI-style ``image_url`` content part with the file inlined as a data URL."""
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}


def shot_messages(
    frames: list[Frame], shot_length_ms: int, taxonomy: TaxonomySnapshot
) -> list[dict[str, Any]]:
    """Chat messages describing one shot with its keyframes and their timestamps."""
    listing = "\n".join(
        f"  frame {i + 1}: {_clock(f.t_ms or 0)} ({f.label})" for i, f in enumerate(frames)
    )
    text = render(
        load_prompt("shot"),
        count=str(len(frames)),
        shot_length=_clock(shot_length_ms),
        frames=listing,
        taxonomy=taxonomy_section(taxonomy),
        flags=flags_section(taxonomy),
    )
    return _messages(text, frames)


def photo_messages(frame: Frame, taxonomy: TaxonomySnapshot) -> list[dict[str, Any]]:
    """Chat messages describing a photograph."""
    text = render(
        load_prompt("photo"), taxonomy=taxonomy_section(taxonomy), flags=flags_section(taxonomy)
    )
    return _messages(text, [frame])


def _messages(text: str, frames: list[Frame]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [image_part(f.path) for f in frames]
    content.append({"type": "text", "text": text})
    return [
        {"role": "system", "content": load_prompt("system")},
        {"role": "user", "content": content},
    ]


async def structured_call[T](
    server: LlamaServer,
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
    parse: Callable[[str], T],
    *,
    max_tokens: int,
    schema_name: str,
) -> tuple[T, CallStats]:
    """Call the model with a schema; one retry at temperature 0 when the answer is unusable.

    Unusable means: ``finish_reason=length`` (truncated), invalid JSON or a contract violation.
    """
    stats = CallStats()
    for temperature in TEMPERATURES:
        stats.attempts += 1
        result: ChatResult = await server.chat(
            messages, schema=schema, schema_name=schema_name,
            temperature=temperature, max_tokens=max_tokens,
        )  # fmt: skip
        stats.seconds += result.seconds
        stats.prompt_tokens += result.prompt_tokens
        stats.completion_tokens += result.completion_tokens
        stats.tokens_per_second = result.tokens_per_second
        if result.finish_reason == "length":
            stats.errors.append(f"truncated after {result.completion_tokens} tokens")
            continue
        try:
            parsed = parse(result.content)
        except SchemaError as exc:
            stats.errors.append(str(exc))
            continue
        stats.valid = True
        return parsed, stats
    raise VisionError("; ".join(stats.errors) or "no valid answer")


async def describe_shot(
    server: LlamaServer,
    frames: list[Frame],
    shot_length_ms: int,
    shot_idx: int,
    taxonomy: TaxonomySnapshot,
    *,
    max_tokens: int,
) -> tuple[VisualDescription, CallStats]:
    """Describe one video shot from up to three keyframes."""
    parsed, stats = await structured_call(
        server, shot_messages(frames, shot_length_ms, taxonomy), build_schema(taxonomy),
        parse_description, max_tokens=max_tokens, schema_name="shot_description",
    )  # fmt: skip
    return to_visual_description(parsed, shot_idx, taxonomy), stats


async def describe_photo(
    server: LlamaServer, frame: Frame, taxonomy: TaxonomySnapshot, *, max_tokens: int
) -> tuple[VisualDescription, CallStats]:
    """Describe a photograph."""
    parsed, stats = await structured_call(
        server, photo_messages(frame, taxonomy), build_schema(taxonomy),
        parse_description, max_tokens=max_tokens, schema_name="photo_description",
    )  # fmt: skip
    return to_visual_description(parsed, None, taxonomy), stats


async def summarize(
    server: LlamaServer, descriptions: list[VisualDescription], duration_ms: int, *, max_tokens: int
) -> tuple[AssetSummary, CallStats]:
    """Summarise a clip from its shot descriptions in one short text-only call."""
    shots = "\n".join(f"Shot {d.shot_idx}: {d.long_en} (DE: {d.short_de})" for d in descriptions)
    text = render(load_prompt("summary"), duration=_clock(duration_ms), shots=shots)
    messages = [
        {"role": "system", "content": load_prompt("system")},
        {"role": "user", "content": text},
    ]
    return await structured_call(
        server, messages, summary_schema(), parse_summary,
        max_tokens=max_tokens, schema_name="asset_summary",
    )  # fmt: skip
