"""Worker results are bounded so one job cannot store unbounded rows."""

import pytest
from pydantic import ValidationError

from rushbase_core.results import MAX_SHOTS, ShotsOutput
from rushbase_core.results_ai import MAX_SEGMENTS, Transcript


def test_shots_are_capped() -> None:
    ok = ShotsOutput.model_validate({"detector": "d", "shots": []})
    assert ok.shots == []
    shot = {"idx": 0, "start_ms": 0, "end_ms": 1, "keyframes": []}
    with pytest.raises(ValidationError, match="at most"):
        ShotsOutput.model_validate({"detector": "d", "shots": [shot] * (MAX_SHOTS + 1)})


def test_transcript_segments_are_capped() -> None:
    model = {"id": "m", "revision": "r"}
    segment = {"start_ms": 0, "end_ms": 1, "text": "x"}
    with pytest.raises(ValidationError, match="at most"):
        Transcript.model_validate({"segments": [segment] * (MAX_SEGMENTS + 1), "model": model})
