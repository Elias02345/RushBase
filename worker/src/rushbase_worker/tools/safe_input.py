"""Hardened input arguments for ffmpeg and ffprobe.

Media on the share is untrusted. FFmpeg can read other local files through playlists and
scripts (HLS, DASH, concat, SDP), so such text formats are refused outright, and only the
``file`` and ``pipe`` protocols are allowed (no network access, no ``data:`` tricks).
"""

from pathlib import Path

from rushbase_worker.tools.process import ToolError

PROTOCOL_WHITELIST = "file,pipe"
_SNIFF_BYTES = 2048
_TEXT_PLAYLIST_MARKERS = (
    b"#extm3u",  # HLS
    b"ffconcat",  # concat demuxer script
    b"<mpd",  # DASH
    b"<?xml",  # DASH / SMIL / other XML manifests
    b"v=0",  # SDP
    b"[playlist]",
    b"ffmetadata",
)


def check_not_a_playlist(path: Path, tool: str = "ffmpeg") -> None:
    """Raise :class:`ToolError` when ``path`` starts like a playlist, manifest or script."""
    try:
        with path.open("rb") as handle:
            head = handle.read(_SNIFF_BYTES)
    except OSError:
        return  # unreadable files fail in the tool with a proper error
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if any(text.startswith(marker) for marker in _TEXT_PLAYLIST_MARKERS):
        raise ToolError(tool, f"refusing to open a playlist or script as media: {path.name}")


def input_args(path: Path, tool: str = "ffmpeg") -> list[str]:
    """``-protocol_whitelist file,pipe -i file:<path>`` after refusing playlist-like content."""
    check_not_a_playlist(path, tool)
    return ["-protocol_whitelist", PROTOCOL_WHITELIST, "-i", f"file:{path}"]
