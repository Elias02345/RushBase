import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from ai_helpers import TAXONOMY, valid_answer
from rushbase_core.results_ai import VisualDescription
from rushbase_worker.runtimes.llama import ChatResult
from rushbase_worker.runtimes.profiles import load_profiles
from rushbase_worker.taxonomy import TaxonomySnapshot
from rushbase_worker.vision.describe import (
    Frame,
    VisionError,
    describe_shot,
    shot_messages,
    summarize,
)
from rushbase_worker.vision.schema import (
    SchemaError,
    build_schema,
    parse_description,
    to_visual_description,
)


def test_schema_is_flat_strict_and_carries_taxonomy_enums() -> None:
    schema = build_schema(TAXONOMY)
    props = schema["properties"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(props)
    assert props["shot_size"]["enum"][0] == "extreme_wide"
    assert props["shot_size_confidence"]["type"] == "number"
    assert props["tax_location"]["items"]["properties"]["value"]["enum"] == ["beach", "forest"]
    assert props["tax_location"]["maxItems"] == 1
    assert props["flag_critical"]["properties"]["value"]["type"] == "boolean"
    empty = build_schema(TaxonomySnapshot())
    assert not [k for k in empty["properties"] if k.startswith(("tax_", "flag_"))]


def test_mapping_builds_contract_with_estimated_pairs_taxonomy_and_flags() -> None:
    description = to_visual_description(parse_description(json.dumps(valid_answer())), 4, TAXONOMY)
    assert isinstance(description, VisualDescription)
    assert description.shot_idx == 4
    assert description.shot_size is not None
    assert (description.shot_size.value.value, description.shot_size.confidence) == ("wide", 0.85)
    assert description.shot_size.source.value == "ai"
    assert description.action is None  # empty string -> no value
    assert description.people_count is not None
    assert description.people_count.value == 2
    assert {(g.field, g.value) for g in description.taxonomy} == {
        ("location", "beach"),
        ("topics", "sport"),
    }
    assert description.flags[0].flag == "critical"
    assert description.flags[0].value is False


def test_confidences_out_of_range_are_clamped_and_missing_text_is_rejected() -> None:
    clamped = parse_description(json.dumps(valid_answer(subject_confidence=1.7)))
    assert clamped.subject_confidence == 1.0
    with pytest.raises(SchemaError):
        parse_description(json.dumps(valid_answer(short_en="")))
    with pytest.raises(SchemaError):
        parse_description("{not json")


class FakeServer:
    """Stands in for LlamaServer: replays scripted answers."""

    def __init__(self, answers: list[tuple[str, str]]) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []
        self.variant = load_profiles().variant("gpu8-4b")

    async def chat(self, messages: list[dict[str, Any]], **kw: Any) -> ChatResult:
        self.calls.append({"messages": messages, **kw})
        content, finish = self.answers.pop(0)
        return ChatResult(content, finish, 100, 50, 0.1, 40.0)


@pytest.fixture
def frames(tmp_path: Path) -> list[Frame]:
    result = []
    for i, position in enumerate(("start", "middle", "end")):
        path = tmp_path / f"{position}.jpg"
        Image.new("RGB", (64, 36), (i * 80, 10, 10)).save(path)
        result.append(Frame(path, 1000 * (i + 1), position))
    return result


async def test_shot_call_sends_three_images_with_timestamps_and_schema(frames: list[Frame]) -> None:
    server = FakeServer([(json.dumps(valid_answer()), "stop")])
    description, stats = await describe_shot(server, frames, 4000, 0, TAXONOMY, max_tokens=900)  # type: ignore[arg-type]
    call = server.calls[0]
    user = call["messages"][1]["content"]
    assert [part["type"] for part in user].count("image_url") == 3
    text = user[-1]["text"]
    assert "00:01.0" in text
    assert "00:03.0" in text
    assert call["schema"]["properties"]["tax_location"]
    assert call["temperature"] == 0.2
    assert stats.valid
    assert stats.attempts == 1
    assert description.short_en == "A beach."


async def test_truncated_output_is_a_failure_and_retried_at_temperature_zero(
    frames: list[Frame],
) -> None:
    server = FakeServer([('{"short_de": "abc', "length"), (json.dumps(valid_answer()), "stop")])
    _, stats = await describe_shot(server, frames, 4000, 0, TAXONOMY, max_tokens=50)  # type: ignore[arg-type]
    assert [c["temperature"] for c in server.calls] == [0.2, 0.0]
    assert stats.attempts == 2
    assert "truncated" in stats.errors[0]


async def test_two_invalid_answers_fail(frames: list[Frame]) -> None:
    server = FakeServer([("{}", "stop"), ("not json", "stop")])
    with pytest.raises(VisionError):
        await describe_shot(server, frames, 4000, 0, TAXONOMY, max_tokens=900)  # type: ignore[arg-type]
    assert not server.answers


async def test_summary_is_a_text_only_call() -> None:
    answer = {"short_de": "k", "short_en": "s", "long_de": "lang", "long_en": "long"}
    server = FakeServer([(json.dumps(answer), "stop")])
    description = to_visual_description(parse_description(json.dumps(valid_answer())), 0, TAXONOMY)
    summary, _ = await summarize(server, [description], 5000, max_tokens=400)  # type: ignore[arg-type]
    assert summary.short_en == "s"
    assert isinstance(server.calls[0]["messages"][1]["content"], str)


def test_prompt_lists_taxonomy_and_flags(frames: list[Frame]) -> None:
    messages = shot_messages(frames, 4000, TAXONOMY)
    text = messages[1]["content"][-1]["text"]
    assert "tax_location" in text
    assert "beach" in text
    assert "flag_critical" in text
    assert "{{" not in text
    assert "German" in messages[0]["content"]
