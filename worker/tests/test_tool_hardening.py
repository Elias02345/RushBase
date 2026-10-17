"""ffmpeg/ffprobe/ExifTool must not be steerable by crafted media (prompt-injection-like input)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from rushbase_worker.config import WorkerSettings
from rushbase_worker.tools.process import ToolError
from rushbase_worker.tools.safe_input import input_args
from rushbase_worker.tools.toolbox import Toolbox

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


LAVFI = "testsrc=duration=1:size=64x64:rate=5"


def _ffmpeg(*output_args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", LAVFI, *output_args], check=True)


@pytest.fixture
def toolbox() -> Toolbox:
    return Toolbox(WorkerSettings(_env_file=None))


def _make_secret_segment(directory: Path) -> Path:
    """A valid media file that a playlist elsewhere on disk tries to pull in."""
    segment = directory / "secret.ts"
    _ffmpeg("-c:v", "mpeg2video", "-f", "mpegts", str(segment))
    return segment


def _hls_playlist(directory: Path, segment: Path) -> Path:
    playlist = directory / "clip.mp4"  # disguised: the name does not matter, the content does
    playlist.write_text(
        f"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\n{segment}\n"
        "#EXT-X-ENDLIST\n"
    )
    return playlist


async def test_ffprobe_refuses_a_playlist_that_references_another_local_file(
    toolbox: Toolbox, tmp_path: Path
) -> None:
    playlist = _hls_playlist(tmp_path, _make_secret_segment(tmp_path))
    with pytest.raises(ToolError, match="playlist"):
        await toolbox.ffprobe(playlist)


async def test_ffmpeg_input_args_refuse_playlists_and_scripts(tmp_path: Path) -> None:
    playlist = _hls_playlist(tmp_path, _make_secret_segment(tmp_path))
    with pytest.raises(ToolError):
        input_args(playlist)
    script = tmp_path / "list.mov"
    script.write_text("ffconcat version 1.0\nfile '/etc/hostname'\n")
    with pytest.raises(ToolError):
        input_args(script)


async def test_normal_media_still_probes_and_decodes(toolbox: Toolbox, tmp_path: Path) -> None:
    clip = tmp_path / "ok.mp4"
    _ffmpeg(str(clip))
    probe = await toolbox.ffprobe(clip)
    assert probe["streams"]
    await toolbox.ffmpeg([*input_args(clip), "-frames:v", "1", "-f", "null", "-"])


def test_input_args_restrict_protocols_and_prefix_file(tmp_path: Path) -> None:
    clip = tmp_path / "ok.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    args = input_args(clip)
    assert args[:2] == ["-protocol_whitelist", "file,pipe"]
    assert args[-2:] == ["-i", f"file:{clip}"]
