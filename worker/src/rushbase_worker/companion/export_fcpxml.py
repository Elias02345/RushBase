"""FCPXML 1.10: the selected shots as clips on one timeline (imports into Resolve and FCP)."""

from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree

from rushbase_worker.companion.clips import Clip
from rushbase_worker.companion.timecode import FrameRate, parse_timecode

PathFn = Callable[[Clip], Path | None]
VERSION = "1.10"
_FORMAT_CODES = {23.976: "2398", 24.0: "24", 25.0: "25", 29.97: "2997", 30.0: "30",
                 50.0: "50", 59.94: "5994", 60.0: "60"}  # fmt: skip


def _format_name(width: int, height: int, rate: FrameRate) -> str | None:
    code = next((c for f, c in _FORMAT_CODES.items() if abs(rate.fps - f) < 0.01), None)
    return f"FFVideoFormat{height}p{code}" if code and height else None


def export_fcpxml(
    clips: list[Clip], resolve_path: PathFn, title: str = "Rushbase selection"
) -> tuple[str, list[str]]:
    """Return ``(FCPXML text, warnings)``.

    Each selected shot becomes an ``asset-clip`` on the spine with in/out at source frames.
    Times are exact rationals (``1001/30000s`` frame durations for 29.97 and so on). Assets
    that have no local file are skipped with a warning (``media-rep`` needs a file URL).
    """
    warnings: list[str] = []
    usable: list[tuple[Clip, Path]] = []
    for clip in clips:
        path = resolve_path(clip)
        if clip.media_type != "video":
            warnings.append(f"{clip.filename}: not a video, skipped")
        elif path is None:
            warnings.append(f"{clip.filename}: no local path (library mount not configured)")
        else:
            usable.append((clip, path))

    root = ElementTree.Element("fcpxml", version=VERSION)
    resources = ElementTree.SubElement(root, "resources")
    formats: dict[tuple[int, int, int, int], str] = {}
    next_id = 1

    def format_id(clip: Clip) -> str:
        nonlocal next_id
        rate = FrameRate.from_float(clip.fps)
        key = (clip.width or 1920, clip.height or 1080, rate.num, rate.den)
        if key not in formats:
            attrs = {"id": f"r{next_id}", "frameDuration": rate.frame_duration,
                     "width": str(key[0]), "height": str(key[1])}  # fmt: skip
            name = _format_name(key[0], key[1], rate)
            if name:
                attrs["name"] = name
            ElementTree.SubElement(resources, "format", attrs)
            formats[key] = attrs["id"]
            next_id += 1
        return formats[key]

    timeline = FrameRate.from_float(usable[0][0].fps if usable else None)
    seq_format = format_id(usable[0][0]) if usable else "r1"
    spine = ElementTree.Element("spine")
    offset = 0
    for clip, path in usable:
        rate = FrameRate.from_float(clip.fps)
        fmt = format_id(clip)
        origin = parse_timecode(clip.start_timecode, rate) or 0
        total = rate.frames(clip.duration_ms or max((s.end_ms for s in clip.shots), default=0))
        asset_id = f"r{next_id}"
        next_id += 1
        asset = ElementTree.SubElement(
            resources, "asset", id=asset_id, name=clip.filename, uid=clip.asset_id,
            start=rate.rational(origin), duration=rate.rational(total), hasVideo="1",
            format=fmt,
        )  # fmt: skip
        ElementTree.SubElement(asset, "media-rep", kind="original-media", src=path.as_uri())
        for shot in clip.selected_shots():
            frames = max(1, rate.frames(shot.end_ms - shot.start_ms))
            length = max(1, timeline.frames(shot.end_ms - shot.start_ms))
            label = clip.filename if clip.selected is None else f"{clip.filename} #{shot.idx}"
            node = ElementTree.SubElement(
                spine, "asset-clip", ref=asset_id, name=label, offset=timeline.rational(offset),
                start=rate.rational(origin + rate.frames(shot.start_ms)),
                duration=rate.rational(frames), format=fmt, tcFormat="NDF",
            )  # fmt: skip
            note = shot.description or clip.description
            if note:
                ElementTree.SubElement(node, "note").text = note
            for keyword in clip.keywords:
                ElementTree.SubElement(
                    node, "keyword", start=rate.rational(origin + rate.frames(shot.start_ms)),
                    duration=rate.rational(frames), value=keyword,
                )  # fmt: skip
            offset += length

    library = ElementTree.SubElement(root, "library")
    event = ElementTree.SubElement(library, "event", name="Rushbase")
    project = ElementTree.SubElement(event, "project", name=title)
    sequence = ElementTree.SubElement(
        project, "sequence", format=seq_format, duration=timeline.rational(offset),
        tcStart="0s", tcFormat="NDF",
    )  # fmt: skip
    sequence.append(spine)
    ElementTree.indent(root)
    body = ElementTree.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + body + "\n", warnings
