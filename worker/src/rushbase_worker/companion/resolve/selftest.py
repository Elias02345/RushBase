"""`rushbase resolve selftest`: end-to-end check in a dedicated throwaway project."""

import subprocess
from pathlib import Path
from typing import Any

from rushbase_worker.companion.resolve.client import ResolveError, Runner, run_bridge
from rushbase_worker.config import WorkerSettings
from rushbase_worker.tools.toolbox import Toolbox

BIN_NAME = "Rushbase self-test"
ASSET_ID = "selftest-0001"


def selftest(
    settings: WorkerSettings, project: str, workdir: Path, *, runner: Runner = subprocess.run
) -> dict[str, Any]:
    """Push a generated 2 s clip into the throwaway ``project`` and verify it by reading back.

    The bridge refuses any project whose name does not start with ``rushbase-test``. Switching
    projects affects the open Resolve session, so the caller must have the owner's consent.
    """
    ffmpeg = Toolbox(settings).ffmpeg_path
    if ffmpeg is None:
        raise ResolveError("ffmpeg is required to generate the self-test clip")
    workdir.mkdir(parents=True, exist_ok=True)
    media = workdir / "rushbase-selftest.mp4"
    generate = runner(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x180:rate=25",
         "-pix_fmt", "yuv420p", str(media)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    if generate.returncode != 0:
        raise ResolveError(f"could not generate the test clip: {generate.stderr[-200:]}")
    clip = {
        "asset_id": ASSET_ID, "path": str(media), "fps": 25.0,
        "metadata": {"Description": "Rushbase self-test", "Keywords": "selftest",
                     "Comments": "rushbase:selftest"},
        "shots": [{"idx": 0, "start_ms": 0, "end_ms": 1000, "description": "first"},
                  {"idx": 1, "start_ms": 1000, "end_ms": 2000, "description": "second"}],
        "clip_color": "Green",
    }  # fmt: skip
    request = {"op": "selftest", "project": project, "bin": BIN_NAME, "clips": [clip],
               "markers": True, "metadata": True}  # fmt: skip
    return run_bridge(settings, request, runner=runner, timeout=600.0)
