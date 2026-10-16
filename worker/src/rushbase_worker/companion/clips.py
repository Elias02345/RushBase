"""Clip model: what exports and Resolve pushes need to know about an asset and its shots."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from rushbase_core import PathOutsideLibraryError, resolve_in_root
from rushbase_worker.config import WorkerSettings


class ShotRef(BaseModel):
    """One shot of an asset (times in milliseconds from the start of the file)."""

    idx: int
    start_ms: int
    end_ms: int
    description: str | None = None
    shot_size: str | None = None


class Clip(BaseModel):
    """An asset (or a selection of its shots) with everything needed offline."""

    asset_id: str
    rel_path: str
    filename: str
    media_type: str = "video"
    fps: float | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    start_timecode: str | None = None
    """Source timecode of the first frame (``HH:MM:SS:FF`` or ``HH:MM:SS.mmm``), if known."""
    title: str | None = None
    description: str | None = None
    keywords: list[str] = Field(default_factory=list)
    paths: dict[str, str] = Field(default_factory=dict)
    shots: list[ShotRef] = Field(default_factory=list)
    review_state: str = "none"
    selected: list[int] | None = None
    """Selected shot indexes; ``None`` means the whole asset."""

    def selected_shots(self) -> list[ShotRef]:
        """The shots to export (the whole file as one pseudo shot when none is selected)."""
        if self.selected is None or not self.shots:
            return [ShotRef(idx=0, start_ms=0, end_ms=self.duration_ms or 0)]
        wanted = set(self.selected)
        return [s for s in self.shots if s.idx in wanted]


def _pick(de: str | None, en: str | None, lang: str) -> str | None:
    return (en or de) if lang == "en" else (de or en)


def clip_from_asset(
    detail: dict[str, Any], selected: list[int] | None = None, lang: str = "de"
) -> Clip:
    """Build a :class:`Clip` from an ``/api/v1/assets/{id}`` document."""
    files = detail.get("files") or []
    primary = next((f for f in files if f.get("role") == "primary"), files[0] if files else {})
    normalized = (detail.get("metadata") or {}).get("normalized") or {}
    video = normalized.get("video") or {}
    ai = detail.get("ai") or {}
    keywords: list[str] = []
    for tag in ai.get("tags") or []:
        label = _pick(tag.get("label_de"), tag.get("label_en"), lang) or tag.get("value")
        if label and label not in keywords:
            keywords.append(str(label))
    shots = [
        ShotRef(
            idx=s["idx"],
            start_ms=s["start_ms"],
            end_ms=s["end_ms"],
            description=_pick(
                (s.get("ai") or {}).get("description_short_de"),
                (s.get("ai") or {}).get("description_short_en"),
                lang,
            ),
            shot_size=(s.get("ai") or {}).get("shot_size"),
        )
        for s in detail.get("shots") or []
    ]
    return Clip(
        asset_id=str(detail["id"]),
        rel_path=primary.get("rel_path") or detail.get("filename", ""),
        filename=detail.get("filename") or Path(primary.get("rel_path", "")).name,
        media_type=detail.get("media_type", "video"),
        fps=detail.get("fps"),
        width=detail.get("width"),
        height=detail.get("height"),
        duration_ms=detail.get("duration_ms"),
        start_timecode=video.get("timecode"),
        title=detail.get("title"),
        description=_pick(ai.get("summary_short_de"), ai.get("summary_short_en"), lang)
        or detail.get("summary"),
        keywords=keywords,
        paths=primary.get("paths") or {},
        shots=shots,
        review_state=detail.get("review_state", "none"),
        selected=selected,
    )


def local_path(settings: WorkerSettings, clip: Clip) -> Path | None:
    """Absolute path of the clip's primary file below this machine's library mount."""
    root = settings.resolved_library_root
    if root is None:
        return None
    try:
        return resolve_in_root(root, clip.rel_path)
    except PathOutsideLibraryError:
        return None
