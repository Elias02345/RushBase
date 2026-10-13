"""Sidecar metadata written next to filed assets (specification section 3).

``<dir>/_meta/<filename>.json`` holds a complete, versioned record of one asset so that the
database can be rebuilt from the share alone (``rushbase-server rebuild-from-sidecars``).
Photos additionally get a standard XMP sidecar (:func:`render_xmp`) that DaVinci Resolve,
Lightroom and similar tools understand.
"""

from datetime import date, datetime
from typing import Any, Final, Literal
from uuid import UUID
from xml.sax.saxutils import escape

from pydantic import BaseModel, ConfigDict, Field

META_DIR_NAME: Final = "_meta"
SIDECAR_SCHEMA: Final = "rushbase.sidecar/1"
XMP_EXTENSION: Final = ".xmp"
RUSHBASE_XMP_SUFFIX: Final = ".rushbase.xmp"


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SidecarFile(_Model):
    """One file of the asset (paths relative to the library root)."""

    role: str
    rel_path: str
    original_rel_path: str
    size: int
    mtime: datetime | None = None
    blake3: str | None = None
    quick_fp: str | None = None
    card_structure_root: str | None = None


class SidecarBatch(_Model):
    """The ingest batch the asset came from."""

    input_path: str
    label: str
    hint_date: date | None = None
    hint_location: str | None = None
    hint_project: str | None = None


class SidecarMetadata(_Model):
    """Normalized probe output plus the complete raw tool output."""

    normalized: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)
    version: int = 1


class SidecarShot(_Model):
    """A detected shot with its keyframes and quality measures."""

    idx: int
    start_ms: int
    end_ms: int
    keyframes: list[dict[str, Any]] = Field(default_factory=list)
    camera_motion: str | None = None
    sharpness: float | None = None
    shake: float | None = None
    exposure: float | None = None
    noise: float | None = None


class SidecarDescriptive(_Model):
    """Descriptive and administrative fields of the asset."""

    title: str | None = None
    summary_short: str | None = None
    summary_long_de: str | None = None
    summary_long_en: str | None = None
    tech_summary_de: str | None = None
    tech_summary_en: str | None = None
    rating: int | None = None
    person_identifiable: str = "unknown"
    consent: str = "unknown"
    consent_ref: str | None = None
    locked: bool = False
    analysis_profile: str | None = None


class SidecarV1(_Model):
    """Complete record of one filed asset (version 1)."""

    schema_: Literal["rushbase.sidecar/1"] = Field(default=SIDECAR_SCHEMA, alias="schema")
    asset_id: UUID
    library_id: UUID | None = None
    media_type: str
    status: str
    content_rel_dir: str
    primary_rel_path: str
    files: list[SidecarFile]
    batch: SidecarBatch | None = None
    metadata: SidecarMetadata = Field(default_factory=SidecarMetadata)
    decode_status: str = "ok"
    derived: dict[str, Any] = Field(default_factory=dict)
    perceptual_hash: str | None = None
    shots: list[SidecarShot] = Field(default_factory=list)
    descriptive: SidecarDescriptive = Field(default_factory=SidecarDescriptive)
    tags: list[dict[str, Any]] = Field(default_factory=list)
    """Placeholder for tags with source and confidence (filled from milestone M4 on)."""
    rule_version: int | None = None
    created_at: datetime
    filed_at: datetime | None = None
    written_at: datetime
    writer: str = "rushbase-server"

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    def to_json(self) -> bytes:
        """Serialise as pretty-printed UTF-8 JSON (stable key order, schema first)."""
        text = self.model_dump_json(by_alias=True, indent=2)
        return (text + "\n").encode("utf-8")


def sidecar_rel_path(directory: str, filename: str) -> str:
    """Library-relative path of the JSON sidecar of ``filename`` inside ``directory``."""
    return f"{directory}/{META_DIR_NAME}/{filename}.json"


def render_xmp(
    *,
    title: str | None,
    description_en: str | None,
    description_de: str | None,
    keywords: list[str],
    rating: int | None,
) -> bytes:
    """Render a minimal XMP packet: title, description, keywords (``dc:subject``) and rating."""

    def alt(tag: str, values: list[tuple[str, str]]) -> str:
        items = "".join(
            f'<rdf:li xml:lang="{lang}">{escape(text)}</rdf:li>' for lang, text in values
        )
        return f"   <{tag}><rdf:Alt>{items}</rdf:Alt></{tag}>\n"

    body = ""
    if title:
        body += alt("dc:title", [("x-default", title)])
    languages: list[tuple[str, str]] = []
    if description_en:
        languages += [("x-default", description_en), ("en", description_en)]
    if description_de:
        languages.append(("de", description_de))
        if not description_en:
            languages.insert(0, ("x-default", description_de))
    if languages:
        body += alt("dc:description", languages)
    if keywords:
        items = "".join(f"<rdf:li>{escape(word)}</rdf:li>" for word in keywords)
        body += f"   <dc:subject><rdf:Bag>{items}</rdf:Bag></dc:subject>\n"
    if rating is not None:
        body += f"   <xmp:Rating>{int(rating)}</xmp:Rating>\n"
    packet = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        '    xmlns:dc="http://purl.org/dc/elements/1.1/"\n'
        '    xmlns:xmp="http://ns.adobe.com/xap/1.0/">\n'
        f"{body}"
        "  </rdf:Description>\n"
        " </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>\n'
    )
    return packet.encode("utf-8")
