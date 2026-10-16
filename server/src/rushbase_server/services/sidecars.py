"""Building and writing sidecars: ``_meta/<filename>.json``, photo XMP, optional central copy.

:func:`build_sidecar` is pure (database rows in, document out); :func:`write_sidecars` is the
synchronous file system part (temp + fsync + rename, run in a worker thread). A failed batch
removes what it wrote, so a unit is either fully documented or not at all.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from uuid import UUID

from rushbase_core import SYSTEM_DIR, resolve_in_root
from rushbase_core.fsops import atomic_write_bytes
from rushbase_core.layout import META_DIR
from rushbase_core.sidecar import (
    META_DIR_NAME,
    RUSHBASE_XMP_SUFFIX,
    XMP_EXTENSION,
    SidecarAI,
    SidecarBatch,
    SidecarDescriptive,
    SidecarFile,
    SidecarMetadata,
    SidecarShot,
    SidecarTag,
    SidecarV1,
    render_xmp,
    sidecar_rel_path,
)
from rushbase_server.models import Asset, AssetFile, AssetMetadata, Batch, FilingRule, Shot


def build_sidecar(
    asset: Asset,
    batch: Batch | None,
    files: Sequence[AssetFile],
    metadata: AssetMetadata | None,
    shots: Sequence[Shot],
    rule: FilingRule | None,
    library_id: UUID | None,
    written_at: datetime,
    tags: Sequence[SidecarTag] = (),
    ai: SidecarAI | None = None,
) -> SidecarV1:
    """Assemble the sidecar document of a (just filed) asset from database rows."""
    primary = next((f for f in files if f.id == asset.primary_file_id), files[0])
    return SidecarV1(
        asset_id=asset.id,
        library_id=library_id,
        media_type=asset.media_type,
        status=asset.status,
        content_rel_dir=asset.content_rel_dir or "",
        primary_rel_path=primary.rel_path,
        files=[
            SidecarFile(
                role=f.role,
                rel_path=f.rel_path,
                original_rel_path=f.original_rel_path,
                size=f.size,
                mtime=f.mtime,
                blake3=f.blake3,
                quick_fp=f.quick_fp,
                card_structure_root=f.card_structure_root,
            )
            for f in sorted(files, key=lambda f: f.rel_path)
        ],
        batch=(
            SidecarBatch(
                input_path=batch.input_path,
                label=batch.label,
                hint_date=batch.hint_date,
                hint_location=batch.hint_location,
                hint_project=batch.hint_project,
            )
            if batch
            else None
        ),
        metadata=SidecarMetadata(
            normalized=metadata.normalized if metadata else {},
            raw=metadata.raw if metadata else {},
            version=metadata.version if metadata else 1,
        ),
        decode_status=asset.decode_status,
        derived=asset.derived,
        perceptual_hash=asset.perceptual_hash,
        shots=[
            SidecarShot(
                idx=s.idx,
                start_ms=s.start_ms,
                end_ms=s.end_ms,
                keyframes=s.keyframes,
                camera_motion=s.camera_motion,
                sharpness=s.sharpness,
                shake=s.shake,
                exposure=s.exposure,
                noise=s.noise,
            )
            for s in sorted(shots, key=lambda s: s.idx)
        ],
        descriptive=SidecarDescriptive(
            title=asset.title,
            summary_short=asset.summary_short,
            summary_long_de=asset.summary_long_de,
            summary_long_en=asset.summary_long_en,
            tech_summary_de=asset.tech_summary_de,
            tech_summary_en=asset.tech_summary_en,
            rating=asset.rating,
            person_identifiable=asset.person_identifiable,
            consent=asset.consent,
            consent_ref=asset.consent_ref,
            locked=asset.locked,
            analysis_profile=asset.analysis_profile,
        ),
        tags=list(tags),
        ai=ai,
        rule_version=rule.version if rule else None,
        created_at=asset.created_at,
        filed_at=asset.filed_at,
        written_at=written_at,
    )


def xmp_for(sidecar: SidecarV1) -> bytes:
    """XMP packet for a photo: title, technical description (en/de) and rating."""
    descriptive = sidecar.descriptive
    return render_xmp(
        title=descriptive.title,
        description_en=descriptive.summary_long_en or descriptive.tech_summary_en,
        description_de=descriptive.summary_long_de or descriptive.tech_summary_de,
        keywords=sorted({t.value for t in sidecar.tags}),
        rating=descriptive.rating,
    )


@dataclass(frozen=True)
class SidecarTask:
    """Everything needed to write the sidecars of one asset."""

    asset_id: UUID
    sidecar: SidecarV1
    meta_directory: str
    """Directory that gets the ``_meta`` folder (the target dir, or the card root)."""
    filename: str
    """File name of the primary file (names the JSON and the XMP)."""
    xmp_directory: str | None = None
    xmp_stem: str | None = None
    camera_xmp_present: bool = False


@dataclass
class WrittenSidecars:
    """Paths written by :func:`write_sidecars` (library-relative)."""

    paths: list[str] = field(default_factory=list)
    directories: list[str] = field(default_factory=list)


def central_rel(asset_id: UUID) -> str:
    """Library-relative path of the optional central copy of a sidecar."""
    return f"{SYSTEM_DIR}/{META_DIR}/{asset_id}.json"


def _existing_owner(path: Path) -> str | None:
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("asset_id"))
    except (OSError, ValueError, AttributeError):
        return None


def json_rel(root: Path, task: SidecarTask) -> str:
    rel = sidecar_rel_path(task.meta_directory, task.filename)
    owner = _existing_owner(resolve_in_root(root, rel))
    if owner is None or owner == str(task.asset_id):
        return rel
    return sidecar_rel_path(task.meta_directory, f"{task.filename}.{str(task.asset_id)[:8]}")


def _xmp_rel(root: Path, task: SidecarTask) -> str:
    assert task.xmp_directory is not None
    assert task.xmp_stem is not None
    plain = f"{task.xmp_directory}/{task.xmp_stem}{XMP_EXTENSION}"
    if not task.camera_xmp_present and not resolve_in_root(root, plain).exists():
        return plain
    return f"{task.xmp_directory}/{task.xmp_stem}{RUSHBASE_XMP_SUFFIX}"


def write_sidecars(root: Path, tasks: Sequence[SidecarTask], central_copy: bool) -> WrittenSidecars:
    """Write JSON sidecars, photo XMP files and (optionally) the central copies.

    On any error the files written so far are removed and the error is re-raised.
    """
    written = WrittenSidecars()
    try:
        for task in tasks:
            data = task.sidecar.to_json()
            json_path = json_rel(root, task)
            meta_dir = resolve_in_root(root, json_path).parent
            if not meta_dir.exists():
                written.directories.append(str(PurePosixPath(json_path).parent))
            atomic_write_bytes(resolve_in_root(root, json_path), data)
            written.paths.append(json_path)
            if task.xmp_directory is not None:
                xmp_rel = _xmp_rel(root, task)
                atomic_write_bytes(resolve_in_root(root, xmp_rel), xmp_for(task.sidecar))
                written.paths.append(xmp_rel)
            if central_copy:
                central = central_rel(task.asset_id)
                atomic_write_bytes(resolve_in_root(root, central), data)
                written.paths.append(central)
    except BaseException:
        remove_written(root, written)
        raise
    return written


def remove_written(root: Path, written: WrittenSidecars) -> None:
    """Delete files written by :func:`write_sidecars` and the ``_meta`` folders it created."""
    for rel in reversed(written.paths):
        resolve_in_root(root, rel).unlink(missing_ok=True)
    for rel in reversed(written.directories):
        try:
            resolve_in_root(root, rel).rmdir()
        except OSError:
            continue
    written.paths.clear()
    written.directories.clear()


__all__ = [
    "META_DIR_NAME",
    "SidecarTask",
    "WrittenSidecars",
    "build_sidecar",
    "central_rel",
    "json_rel",
    "remove_written",
    "write_sidecars",
    "xmp_for",
]
