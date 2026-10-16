"""CMX 3600 EDL: a simple cut list of the selected shots."""

import re

from rushbase_worker.companion.clips import Clip
from rushbase_worker.companion.timecode import FrameRate, format_timecode, parse_timecode

RECORD_START = "01:00:00:00"
REEL_MAX = 8


def reel_name(filename: str, taken: set[str]) -> str:
    """Upper-case reel name of at most 8 characters, unique within the list."""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    base = re.sub(r"[^A-Za-z0-9]", "", stem).upper()[:REEL_MAX] or "REEL"
    name, counter = base, 1
    while name in taken:
        suffix = str(counter)
        name = base[: REEL_MAX - len(suffix)] + suffix
        counter += 1
    taken.add(name)
    return name


def export_edl(clips: list[Clip], title: str = "Rushbase selection") -> tuple[str, list[str]]:
    """Return ``(edl text, warnings)``; non-video assets are skipped with a warning.

    Timecode is non-drop-frame; the source timecode starts at the clip's recorded start
    timecode (or ``00:00:00:00``). The timeline rate is the rate of the first clip.
    """
    warnings: list[str] = []
    video = [c for c in clips if c.media_type == "video"]
    warnings += [f"{c.filename}: not a video, skipped" for c in clips if c.media_type != "video"]
    lines = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    if not video:
        return "\n".join(lines) + "\n", warnings
    timeline = FrameRate.from_float(video[0].fps)
    record = parse_timecode(RECORD_START, timeline) or 0
    taken: set[str] = set()
    event = 0
    for clip in video:
        rate = FrameRate.from_float(clip.fps)
        origin = parse_timecode(clip.start_timecode, rate) or 0
        reel = reel_name(clip.filename, taken)
        for shot in clip.selected_shots():
            length = max(1, rate.frames(shot.end_ms - shot.start_ms))
            rec_length = max(1, timeline.frames(shot.end_ms - shot.start_ms))
            src_in = origin + rate.frames(shot.start_ms)
            event += 1
            lines.append(
                f"{event:03d}  {reel:<8} V     C        "
                f"{format_timecode(src_in, rate)} {format_timecode(src_in + length, rate)} "
                f"{format_timecode(record, timeline)} {format_timecode(record + rec_length, timeline)}"
            )
            lines.append(f"* FROM CLIP NAME: {clip.filename}")
            if clip.selected is not None and clip.shots:
                lines.append(
                    f"* COMMENT: shot {shot.idx}"
                    + (f" {shot.description}" if shot.description else "")
                )
            lines.append("")
            record += rec_length
    return "\n".join(lines) + "\n", warnings
