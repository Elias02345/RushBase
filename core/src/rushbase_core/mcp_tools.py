"""MCP tool contract shared by the server and the companion app's local MCP server.

Every tool has an English name, a precise description, the OAuth scope it needs and a small
pydantic input model (the JSON schema is kept below :data:`MAX_SCHEMA_BYTES`). Results are JSON
text of at most :data:`MAX_RESULT_CHARS` characters, paginated with opaque cursors.

There is deliberately no tool that deletes, moves or renames files (architecture invariant 7);
:data:`FORBIDDEN_TOOL_WORDS` is checked by the test suite against the registry.
"""

import json
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_RESULT_CHARS: Final = 30_000
MAX_SCHEMA_BYTES: Final = 4096
MAX_IMAGES: Final = 6
MAX_IMAGE_EDGE: Final = 512

SCOPES: Final = ("read", "annotate", "taxonomy", "rules", "needs", "processing")
ALL_SCOPES: Final = (*SCOPES, "offline_access")
"""``offline_access`` asks for a refresh token; it grants no tool."""

FORBIDDEN_TOOL_WORDS: Final = ("delete", "remove", "move", "rename", "trash", "purge", "unlink")

Cursor = Annotated[
    str | None, Field(default=None, description="Opaque cursor from `next_cursor` of a prior page.")
]
AssetId = Annotated[str, Field(description="Asset UUID.", min_length=32, max_length=36)]


class Strict(BaseModel):
    """Base of all tool inputs: unknown arguments are rejected."""

    model_config = ConfigDict(extra="forbid")


class MediaFilters(Strict):
    """Filters shared by search, counts, coverage and needs items (all optional)."""

    media_type: Literal["video", "photo", "audio"] | None = None
    tags: list[str] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Taxonomy filters `field:value`, e.g. `location:harbour`. Values of one field are "
            "alternatives, different fields combine with AND. See `taxonomy_list`."
        ),
    )
    camera_model: str | None = None
    lens_model: str | None = None
    log_profile: str | None = None
    orientation: Literal["landscape", "portrait", "square"] | None = None
    date_from: date | None = Field(default=None, description="Capture date, inclusive.")
    date_to: date | None = Field(default=None, description="Capture date, inclusive.")
    shot_size: str | None = None
    camera_angle: str | None = None
    interior_exterior: str | None = None
    time_of_day: str | None = None
    review_state: Literal["none", "pending", "done"] | None = None
    consent: Literal["yes", "no", "unknown"] | None = None


class SearchMedia(Strict):
    """Input of ``search_media``."""

    q: str = Field(min_length=1, max_length=200, description="Natural-language query.")
    level: Literal["asset", "shot"] = Field(
        default="asset", description="`shot` returns scenes with timecodes."
    )
    filters: MediaFilters = Field(default_factory=MediaFilters)
    include_thumbnails: bool = Field(
        default=False, description="Attach small JPEG thumbnails of the first hits (max 6)."
    )
    limit: int = Field(default=10, ge=1, le=30)
    cursor: Cursor = None


class GetAsset(Strict):
    """Input of ``get_asset``."""

    asset_id: AssetId
    transcript_offset: int = Field(default=0, ge=0, description="First transcript segment.")
    transcript_limit: int = Field(default=40, ge=0, le=200)


class GetKeyframes(Strict):
    """Input of ``get_keyframes``."""

    asset_id: AssetId
    shot_idx: int | None = Field(
        default=None, ge=0, description="Only this shot; default: first keyframe of each shot."
    )
    limit: int = Field(default=4, ge=1, le=6)


class FindSimilar(Strict):
    """Input of ``find_similar``."""

    asset_id: AssetId
    shot_idx: int | None = Field(default=None, ge=0, description="Compare a single shot.")
    filters: MediaFilters = Field(default_factory=MediaFilters)
    limit: int = Field(default=10, ge=1, le=30)
    cursor: Cursor = None


class ListFacets(Strict):
    """Input of ``list_facets``."""

    filters: MediaFilters = Field(default_factory=MediaFilters)
    q: str | None = Field(default=None, max_length=200)


class FacetCounts(Strict):
    """Input of ``facet_counts``."""

    facet: str = Field(max_length=40, description="Facet key from `list_facets`.")
    filters: MediaFilters = Field(default_factory=MediaFilters)
    q: str | None = Field(default=None, max_length=200)


class LibraryStats(Strict):
    """Input of ``library_stats`` (no arguments)."""


class CoverageReport(Strict):
    """Input of ``coverage_report``."""

    rows: str = Field(
        max_length=40,
        description="Row dimension: technical (media_type, camera_model, lens_model, log_profile, "
        "orientation, codec, shot_size, camera_angle, interior_exterior, time_of_day) or a "
        "taxonomy field key.",
    )
    columns: str = Field(max_length=40, description="Column dimension, same options as `rows`.")
    filters: MediaFilters = Field(default_factory=MediaFilters)
    include_last_capture: bool = Field(
        default=False, description="Add the newest capture date per cell."
    )


class GapItem(Strict):
    """One wanted shot of a shot list."""

    description: str = Field(min_length=1, max_length=300)
    filters: MediaFilters = Field(default_factory=MediaFilters)
    count: int = Field(default=1, ge=1, le=50, description="How many matches are wanted.")


class GapAnalysis(Strict):
    """Input of ``gap_analysis``."""

    items: list[GapItem] = Field(min_length=1, max_length=25)
    candidates: int = Field(default=3, ge=1, le=10, description="Top candidates per item.")


class NeedsCreate(Strict):
    """Input of ``needs_create``."""

    title: str = Field(min_length=1, max_length=200)
    purpose: str = Field(default="", max_length=2000)
    items: list[GapItem] = Field(default_factory=list, max_length=50)


class NeedsList(Strict):
    """Input of ``needs_list``."""

    status: Literal["open", "closed"] | None = None
    limit: int = Field(default=20, ge=1, le=50)
    cursor: Cursor = None


class NeedsGet(Strict):
    """Input of ``needs_get``."""

    need_id: str = Field(min_length=32, max_length=36)
    include_thumbnails: bool = False


class NeedsUpdate(Strict):
    """Input of ``needs_update``."""

    need_id: str = Field(min_length=32, max_length=36)
    title: str | None = Field(default=None, max_length=200)
    purpose: str | None = Field(default=None, max_length=2000)
    add_items: list[GapItem] = Field(default_factory=list, max_length=50)
    item_id: str | None = Field(default=None, description="Item to change (with the fields below).")
    item_status: Literal["open", "fulfilled", "dropped"] | None = None
    item_count: int | None = Field(default=None, ge=1, le=50)


class NeedsLinkCandidate(Strict):
    """Input of ``needs_link_candidate``."""

    item_id: str = Field(min_length=32, max_length=36)
    asset_id: AssetId
    shot_idx: int | None = Field(default=None, ge=0)
    status: Literal["candidate", "confirmed", "rejected"] = "candidate"


class NeedsClose(Strict):
    """Input of ``needs_close``."""

    need_id: str = Field(min_length=32, max_length=36)
    reopen: bool = Field(default=False, description="Reopen a closed list instead.")


class AnnotateAsset(Strict):
    """Input of ``annotate_asset``."""

    asset_id: AssetId
    add_tags: list[str] = Field(
        default_factory=list, max_length=30, description="`field:value` of approved values."
    )
    remove_tags: list[str] = Field(default_factory=list, max_length=30)
    rating: int | None = Field(default=None, ge=0, le=5)
    note: str | None = Field(default=None, max_length=2000, description="Free-text note.")
    locked: bool | None = Field(default=None, description="Protect from reprocessing.")
    consent: Literal["yes", "no", "unknown"] | None = None
    consent_ref: str | None = Field(default=None, max_length=300)


class TaxonomyList(Strict):
    """Input of ``taxonomy_list``."""

    field: str | None = Field(default=None, max_length=40, description="Only this field key.")
    status: Literal["proposed", "approved", "deprecated"] | None = None


class TaxonomyProposeValue(Strict):
    """Input of ``taxonomy_propose_value``."""

    field: str = Field(max_length=40)
    value: str = Field(min_length=1, max_length=100)
    label_de: str | None = Field(default=None, max_length=100)
    label_en: str | None = Field(default=None, max_length=100)
    synonyms: list[str] = Field(default_factory=list, max_length=20)
    description: str = Field(default="", max_length=500)


class TaxonomyApprove(Strict):
    """Input of ``taxonomy_approve``."""

    value_id: str = Field(min_length=32, max_length=36)


class TaxonomyMerge(Strict):
    """Input of ``taxonomy_merge``."""

    value_id: str = Field(min_length=32, max_length=36, description="Value to fold in.")
    target_id: str = Field(min_length=32, max_length=36, description="Value that remains.")


class TaxonomyAddSynonym(Strict):
    """Input of ``taxonomy_add_synonym``."""

    value_id: str = Field(min_length=32, max_length=36)
    synonym: str = Field(min_length=1, max_length=100)


class RulesGet(Strict):
    """Input of ``rules_get`` (no arguments)."""


class RulesPropose(Strict):
    """Input of ``rules_propose``: stores an inactive version and returns a dry run."""

    template: str = Field(min_length=1, max_length=500)
    fallbacks: dict[str, str] = Field(default_factory=dict, max_length=20)
    note: str = Field(default="", max_length=500)
    dry_run_limit: int = Field(default=50, ge=1, le=200)


class RulesActivate(Strict):
    """Input of ``rules_activate``."""

    rule_id: str = Field(min_length=32, max_length=36)


class ProcessingStatus(Strict):
    """Input of ``processing_status`` (no arguments)."""


class Reprocess(Strict):
    """Input of ``reprocess``: queues a stage again. Files are never touched."""

    stage: Literal["probe", "hash", "preview", "shots", "audio", "embed", "vision", "classify"]
    asset_ids: list[str] = Field(default_factory=list, max_length=200)
    filters: MediaFilters | None = Field(
        default=None, description="Select by filter instead of ids (max 500 assets)."
    )


@dataclass(frozen=True)
class ToolSpec:
    """Name, description, scope and input model of one tool."""

    name: str
    description: str
    scope: str
    model: type[Strict]
    writes: bool = False

    def input_schema(self) -> dict[str, Any]:
        """JSON schema of the input."""
        return self.model.model_json_schema()


def _spec(
    name: str, scope: str, model: type[Strict], description: str, writes: bool = False
) -> ToolSpec:
    return ToolSpec(name, description, scope, model, writes)


TOOLS: Final[tuple[ToolSpec, ...]] = (
    _spec(
        "search_media",
        "read",
        SearchMedia,
        "Hybrid search (full text + semantic) over the media library. Returns assets or shots "
        "with summary, technical data and score; optional thumbnails. Paginated.",
    ),
    _spec(
        "get_asset",
        "read",
        GetAsset,
        "All data of one asset: technical metadata, AI summaries, tags with source, shots with "
        "descriptions, review state, paths, and a page of the transcript.",
    ),
    _spec(
        "get_keyframes",
        "read",
        GetKeyframes,
        "Keyframe images (JPEG, at most 512 px, max 6) of an asset or one shot, with timecodes.",
    ),
    _spec(
        "find_similar",
        "read",
        FindSimilar,
        "Assets or shots that look like a given asset or shot (stored image vectors). Paginated.",
    ),
    _spec(
        "list_facets",
        "read",
        ListFacets,
        "Available filter dimensions with value counts for the current filters/query "
        "(technical fields and taxonomy fields).",
    ),
    _spec(
        "facet_counts",
        "read",
        FacetCounts,
        "Value counts of one facet under the given filters and query.",
    ),
    _spec(
        "library_stats",
        "read",
        LibraryStats,
        "Library size: assets by type and status, storage, duration, capture year range.",
    ),
    _spec(
        "coverage_report",
        "read",
        CoverageReport,
        "Matrix of asset counts for two fields (technical or taxonomy), optionally with the "
        "newest capture date per cell. Shows what the library covers and where it is thin.",
    ),
    _spec(
        "gap_analysis",
        "read",
        GapAnalysis,
        "For each wanted shot (description + filters + count) return the best matching library "
        "assets with scores, or `missing` when too few match.",
    ),
    _spec(
        "needs_create",
        "needs",
        NeedsCreate,
        "Create a needs list (what footage is still missing) with optional items.",
        True,
    ),
    _spec("needs_list", "needs", NeedsList, "List needs lists with item counts. Paginated."),
    _spec(
        "needs_get",
        "needs",
        NeedsGet,
        "One needs list with items and candidates (auto-matched, Claude or human).",
    ),
    _spec(
        "needs_update",
        "needs",
        NeedsUpdate,
        "Rename a needs list, add items, or change one item's status or wanted count.",
        True,
    ),
    _spec(
        "needs_link_candidate",
        "needs",
        NeedsLinkCandidate,
        "Attach an asset (or shot) as candidate for a needs item, or confirm/reject it.",
        True,
    ),
    _spec(
        "needs_close",
        "needs",
        NeedsClose,
        "Close a needs list (or reopen it). Closed lists receive no new auto-matches.",
        True,
    ),
    _spec(
        "annotate_asset",
        "annotate",
        AnnotateAsset,
        "Add or remove tags, set rating, note, lock or consent on an asset. Source `claude`; "
        "human edits always win over Claude, Claude wins over AI.",
        True,
    ),
    _spec(
        "taxonomy_list",
        "read",
        TaxonomyList,
        "The controlled vocabulary: fields, values, synonyms, status and asset counts.",
    ),
    _spec(
        "taxonomy_propose_value",
        "taxonomy",
        TaxonomyProposeValue,
        "Propose a new vocabulary value (status `proposed`, a person approves it).",
        True,
    ),
    _spec(
        "taxonomy_approve",
        "taxonomy",
        TaxonomyApprove,
        "Approve a proposed value (editor or admin only).",
        True,
    ),
    _spec(
        "taxonomy_merge",
        "taxonomy",
        TaxonomyMerge,
        "Merge one value into another of the same field (admin only). Tags move over.",
        True,
    ),
    _spec(
        "taxonomy_add_synonym",
        "taxonomy",
        TaxonomyAddSynonym,
        "Add a synonym to a vocabulary value (admin only).",
        True,
    ),
    _spec(
        "rules_get",
        "read",
        RulesGet,
        "The active filing rule (template for the folder structure of new material) and history.",
    ),
    _spec(
        "rules_propose",
        "rules",
        RulesPropose,
        "Store a candidate filing rule (inactive) and return a dry run: where recent assets "
        "would be filed, conflicts and warnings. Nothing moves.",
        True,
    ),
    _spec(
        "rules_activate",
        "rules",
        RulesActivate,
        "Activate a stored rule version. Applies to material filed afterwards only.",
        True,
    ),
    _spec(
        "processing_status",
        "read",
        ProcessingStatus,
        "Queue backlog per stage, online workers, asset counts by status, quarantine.",
    ),
    _spec(
        "reprocess",
        "processing",
        Reprocess,
        "Queue an analysis stage again for given assets or a filter selection. Never touches "
        "or moves files.",
        True,
    ),
)

BY_NAME: Final = {tool.name: tool for tool in TOOLS}


def schema_size(spec: ToolSpec) -> int:
    """Serialized size of the tool's input schema in bytes."""
    return len(json.dumps(spec.input_schema(), separators=(",", ":")).encode())
