"""Pure grouping of ingested files into assets.

The input is a set of library-relative file paths below one batch folder; the output
describes which files form one asset, which role each plays and which card structure (a
directory that must be kept intact when filing) an asset belongs to. No file system access
happens here, so the function is reused unchanged by the server scanner, the worker and
the CLI.

Rules (see ADR-0005 and the product specification, section 4):

* Same directory + same stem (NFC, case-insensitive): RAW + JPEG + sidecars are one asset.
* Camera card structures (``PRIVATE/M4ROOT``, ``AVCHD``/``BDMV``, ``XDROOT``, ``DCIM``) are
  kept as a unit: every clip becomes its own asset that references the card root.
* ``*.RDC`` directories (RED) are one clip each; all segments belong to one asset.
* Numbered frames (``dng``, ``dpx``, ``exr``, ... with at least ``SEQUENCE_MIN_DIGITS`` digits
  and ``SEQUENCE_MIN_FRAMES`` consecutive frames) form one video asset whose directory is a unit.
  The thresholds keep ordinary photo series (``IMG_0001.dng`` ...) from being merged.
"""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

from rushbase_core.media import (
    RAW_PHOTO_EXTENSIONS,
    SEQUENCE_EXTENSIONS,
    SIDECAR_EXTENSIONS,
    FileRole,
    MediaType,
    extension_of,
    media_type_for,
)

SEQUENCE_MIN_FRAMES: Final = 12
SEQUENCE_MIN_DIGITS: Final = 5
_CARD_MARKERS: Final = frozenset({"avchd", "bdmv", "xdroot", "dcim"})
_PRIVATE_CHILDREN: Final = frozenset({"m4root", "avchd"})
_PROXY_DIRS: Final = frozenset({"sub", "thmbnl", "proxy", "proxies"})
_FRAME_RE: Final = re.compile(r"^(?P<prefix>.*?)(?P<num>\d+)$")
_RASTER_PRIORITY: Final = ("heic", "heif", "tif", "tiff", "png", "webp", "jpg", "jpeg")


@dataclass(frozen=True)
class GroupMember:
    """One file of an asset group."""

    rel_path: str
    role: FileRole


@dataclass(frozen=True)
class AssetGroup:
    """Files that together form one asset. ``members[0]`` is the primary file."""

    media_type: MediaType
    members: tuple[GroupMember, ...]
    card_structure_root: str | None = None

    @property
    def primary(self) -> GroupMember:
        """The file that identifies the asset."""
        return self.members[0]

    @property
    def paths(self) -> tuple[str, ...]:
        """All member paths."""
        return tuple(member.rel_path for member in self.members)


@dataclass(frozen=True)
class GroupingResult:
    """Output of :func:`group_files`."""

    groups: tuple[AssetGroup, ...]
    ungrouped: tuple[str, ...]
    """Sidecars without a matching media file (they wait for their partner to arrive)."""


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _key(value: str) -> str:
    return _nfc(value).casefold()


def _stem(name: str) -> str:
    return PurePosixPath(name).stem


def _sidecar_role(name: str) -> FileRole:
    ext = extension_of(name)
    if ext in ("lrf", "lrv"):
        return FileRole.PROXY_CAMERA
    if ext == "srt":
        return FileRole.TELEMETRY
    return FileRole.SIDECAR


def _raster_rank(name: str) -> int:
    ext = extension_of(name)
    return _RASTER_PRIORITY.index(ext) if ext in _RASTER_PRIORITY else len(_RASTER_PRIORITY)


def _unit_root(parts: tuple[str, ...]) -> tuple[str, ...] | None:
    """Return the card-structure root (relative to the batch folder) containing a file."""
    dirs = parts[:-1]
    for index, name in enumerate(dirs):
        lower = name.lower()
        if lower.endswith(".rdc"):
            return dirs[: index + 1]
        is_private = lower == "private" and index + 1 < len(dirs)
        is_private = is_private and dirs[index + 1].lower() in _PRIVATE_CHILDREN
        if is_private or lower in _CARD_MARKERS:
            # Keep the whole card dump when it sits in a sub folder, else the marker itself.
            return dirs[:index] if index >= 1 else dirs[:1]
    return None


def _join(*parts: str) -> str:
    return "/".join(p for p in parts if p)


def _stem_groups(
    paths: list[str], root: str | None, *, claim_unknown: bool = True
) -> tuple[list[AssetGroup], list[str]]:
    """Group files of (possibly several) directories by directory + stem."""
    buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
    for path in paths:
        pure = PurePosixPath(path)
        buckets[(_key(str(pure.parent)), _key(pure.stem))].append(path)
    groups: list[AssetGroup] = []
    orphans: list[str] = []
    for key in sorted(buckets):
        group = _stem_bucket(sorted(buckets[key]), root, claim_unknown=claim_unknown)
        if group is None:
            orphans.extend(sorted(buckets[key]))
        else:
            groups.append(group)
    return groups, orphans


def _stem_bucket(paths: list[str], root: str | None, *, claim_unknown: bool) -> AssetGroup | None:
    """Build one asset from files that share directory and stem (``None``: only sidecars)."""
    videos = [p for p in paths if media_type_for(p) is MediaType.VIDEO]
    raws = [p for p in paths if extension_of(p) in RAW_PHOTO_EXTENSIONS]
    rasters = sorted(
        (
            p
            for p in paths
            if media_type_for(p) is MediaType.PHOTO and extension_of(p) not in RAW_PHOTO_EXTENSIONS
        ),
        key=lambda p: (_raster_rank(p), p),
    )
    audios = [p for p in paths if media_type_for(p) is MediaType.AUDIO]
    sidecars = [p for p in paths if extension_of(p) in SIDECAR_EXTENSIONS]
    unknown = [p for p in paths if media_type_for(p) is MediaType.OTHER and p not in sidecars]
    members: list[GroupMember]
    if videos:
        media_type = MediaType.VIDEO
        members = [GroupMember(videos[0], FileRole.PRIMARY)]
        members += [GroupMember(p, FileRole.MEMBER) for p in videos[1:] + raws + rasters]
        members += [GroupMember(p, FileRole.AUDIO_SYNC) for p in audios]
    elif raws:
        media_type = MediaType.PHOTO
        members = [GroupMember(raws[0], FileRole.RAW)]
        members += [GroupMember(p, FileRole.MEMBER) for p in raws[1:]]
        members += [GroupMember(p, FileRole.JPEG) for p in rasters]
        members += [GroupMember(p, FileRole.MEMBER) for p in audios]
    elif rasters:
        media_type = MediaType.PHOTO
        members = [GroupMember(rasters[0], FileRole.PRIMARY)]
        members += [GroupMember(p, FileRole.MEMBER) for p in rasters[1:] + audios]
    elif audios:
        media_type = MediaType.AUDIO
        members = [GroupMember(audios[0], FileRole.PRIMARY)]
        members += [GroupMember(p, FileRole.MEMBER) for p in audios[1:]]
    elif unknown and claim_unknown:
        media_type = MediaType.OTHER
        members = [GroupMember(unknown[0], FileRole.PRIMARY)]
        members += [GroupMember(p, FileRole.MEMBER) for p in unknown[1:]]
        unknown = []
    else:
        return None
    members += [GroupMember(p, _sidecar_role(p)) for p in sidecars]
    members += [GroupMember(p, FileRole.MEMBER) for p in unknown]
    return AssetGroup(media_type, tuple(members), root)


def _find_sequences(paths: list[str], batch_root: str) -> tuple[list[AssetGroup], list[str]]:
    """Extract numbered image sequences from ``paths``; return them plus the leftovers."""
    by_dir: dict[str, dict[tuple[str, str, int], list[tuple[int, str]]]] = defaultdict(dict)
    for path in paths:
        pure = PurePosixPath(path)
        ext = extension_of(path)
        match = _FRAME_RE.match(pure.stem)
        if ext not in SEQUENCE_EXTENSIONS or match is None:
            continue
        digits = match["num"]
        if len(digits) < SEQUENCE_MIN_DIGITS:
            continue
        series = (_key(match["prefix"]), ext, len(digits))
        by_dir[str(pure.parent)].setdefault(series, []).append((int(digits), path))
    groups: list[AssetGroup] = []
    claimed: set[str] = set()
    for directory, series_map in sorted(by_dir.items()):
        for frames in series_map.values():
            frames.sort()
            run: list[tuple[int, str]] = []
            for frame in [*frames, (-2, "")]:  # sentinel flushes the last run
                if run and frame[0] == run[-1][0] + 1:
                    run.append(frame)
                    continue
                if len(run) >= SEQUENCE_MIN_FRAMES:
                    root = None if directory == batch_root else directory
                    members = [GroupMember(run[0][1], FileRole.PRIMARY)]
                    members += [GroupMember(p, FileRole.MEMBER) for _, p in run[1:]]
                    groups.append(AssetGroup(MediaType.VIDEO, tuple(members), root))
                    claimed.update(p for _, p in run)
                run = [frame]
    return groups, [p for p in paths if p not in claimed]


def _attach_prefix(clips: list[AssetGroup], extra: list[str]) -> tuple[list[AssetGroup], list[str]]:
    """Attach card files to the clip whose stem prefixes them (``C0001S03.MP4`` -> ``C0001``)."""
    stems = sorted(
        ((_key(_stem(g.primary.rel_path)), index) for index, g in enumerate(clips)),
        key=lambda item: -len(item[0]),
    )
    attached: dict[int, list[GroupMember]] = defaultdict(list)
    leftovers: list[str] = []
    for path in sorted(extra):
        file_stem = _key(_stem(path))
        target = next(
            (
                index
                for stem, index in stems
                if stem and file_stem.startswith(stem) and not file_stem[len(stem) :][:1].isdigit()
            ),
            None,
        )
        if target is None:
            leftovers.append(path)
            continue
        is_media = media_type_for(path) is not MediaType.OTHER
        attached[target].append(
            GroupMember(path, FileRole.PROXY_CAMERA if is_media else _sidecar_role(path))
        )
    result = [
        AssetGroup(g.media_type, g.members + tuple(attached[i]), g.card_structure_root)
        for i, g in enumerate(clips)
    ]
    return result, leftovers


def _group_unit(root: str, paths: list[str]) -> tuple[list[AssetGroup], list[str]]:
    """Group the files of one card structure; every clip is its own asset."""
    if root.lower().endswith(".rdc"):
        ordered = sorted(paths)
        clips = [p for p in ordered if extension_of(p) == "r3d"] or ordered
        members = [GroupMember(clips[0], FileRole.PRIMARY)]
        for path in ordered:
            if path == clips[0]:
                continue
            if extension_of(path) == "r3d":
                members.append(GroupMember(path, FileRole.MEMBER))
            elif media_type_for(path) is MediaType.VIDEO:
                members.append(GroupMember(path, FileRole.PROXY_CAMERA))
            else:
                members.append(GroupMember(path, _sidecar_role(path)))
        return [AssetGroup(MediaType.VIDEO, tuple(members), root)], []
    proxies = [
        p
        for p in paths
        if media_type_for(p) is not MediaType.OTHER
        and any(part.lower() in _PROXY_DIRS for part in PurePosixPath(p).parts[:-1])
    ]
    main = [p for p in paths if p not in set(proxies)]
    groups, orphans = _stem_groups(main, root, claim_unknown=False)
    groups, leftovers = _attach_prefix(groups, orphans + proxies)
    if leftovers and groups:
        first = min(range(len(groups)), key=lambda i: groups[i].primary.rel_path)
        extra = tuple(GroupMember(p, FileRole.MEMBER) for p in leftovers)
        g = groups[first]
        groups[first] = AssetGroup(g.media_type, g.members + extra, g.card_structure_root)
        leftovers = []
    return groups, leftovers


def group_files(rel_paths: Iterable[str], *, batch_root: str) -> GroupingResult:
    """Group library-relative ``rel_paths`` (all below ``batch_root``) into assets.

    ``batch_root`` is the batch folder (``Input/<folder>``) or ``Input`` for loose files.
    The result is deterministic: groups are ordered by primary path.
    """
    base = _nfc(batch_root).strip("/")
    plain: list[str] = []
    units: dict[str, list[str]] = defaultdict(list)
    for path in sorted(set(rel_paths)):
        relative = PurePosixPath(path).relative_to(base).parts
        unit = _unit_root(relative)
        if unit is None:
            plain.append(path)
        else:
            units[_join(base, *unit)].append(path)
    groups: list[AssetGroup] = []
    ungrouped: list[str] = []
    for root, paths in sorted(units.items()):
        unit_groups, unit_left = _group_unit(root, paths)
        groups.extend(unit_groups)
        ungrouped.extend(unit_left)
    sequences, rest = _find_sequences(plain, base)
    groups.extend(sequences)
    stem_groups, orphans = _stem_groups(rest, None)
    groups.extend(stem_groups)
    ungrouped.extend(orphans)
    groups.sort(key=lambda g: g.primary.rel_path)
    return GroupingResult(tuple(groups), tuple(sorted(ungrouped)))
