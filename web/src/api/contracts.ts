/**
 * Request and response shapes of the read and admin APIs.
 *
 * Everything is an alias of the generated OpenAPI types in `@rushbase/api-client`, so the web
 * app cannot drift from the server (the MSW mocks are typed from the same schema). Only the
 * narrower UI unions below are hand-written; the server sends them as plain strings.
 */
import type { components, paths } from "@rushbase/api-client";

type Schemas = components["schemas"];

export type MediaType = "video" | "photo" | "audio" | "other";
export type AssetStatus =
  "ingesting" | "processing" | "review" | "filed" | "quarantined" | "trashed" | "duplicate";
export type Orientation = "landscape" | "portrait" | "square";

/** Sort values accepted by `GET /api/v1/assets`; a leading `-` means descending. */
export const SORT_KEYS = [
  "-date",
  "date",
  "-size",
  "-duration",
  "filename",
  "-created_at",
] as const;
export type SortKey = (typeof SORT_KEYS)[number];

export type AssetSummary = Schemas["AssetSummary"];
export type AssetPage = Schemas["AssetPage"];
export type AssetFile = Schemas["FileOut"];
export type Keyframe = Schemas["KeyframeOut"];
export type Shot = Schemas["ShotOut"];
export type HistoryEntry = Schemas["HistoryOut"];
export type AssetDetail = Schemas["AssetDetail"];
export type Facet = Schemas["Facet"];
export type FacetValue = Schemas["FacetValue"];
export type FacetsResponse = Schemas["FacetsOut"];
export type StatsResponse = Schemas["StatsOut"];
export type EnrollmentTokenInfo = Schemas["EnrollmentTokenOut"];
export type FilingRule = Schemas["RuleOut"];
export type FilingDryRun = Schemas["DryRunOut"];
export type FilingDryRunItem = Schemas["DryRunItemOut"];
export type LibrarySettings = Schemas["LibrarySettings"];
export type QuarantineItem = Schemas["QuarantineItem"];

export type SearchOut = Schemas["SearchOut"];
export type SearchHit = Schemas["SearchHit"];
export type ShotHit = Schemas["ShotHit"];
export type SearchCapabilities = Schemas["CapabilitiesOut"];
export type AiInfo = Schemas["AiOut"];
export type ShotAi = Schemas["ShotAiOut"];
export type TagInfo = Schemas["TagOut"];
export type Transcript = Schemas["TranscriptOut"];
export type TranscriptSegment = Schemas["SegmentOut"];
export type AudioEvent = Schemas["AudioEventOut"];
export type Taxonomy = Schemas["TaxonomyOut"];
export type TaxonomyField = Schemas["FieldOut"];
export type TaxonomyValue = Schemas["ValueOut"];
export type Proposal = Schemas["ProposalOut"];
export type ReviewPage = Schemas["ReviewPage"];
export type ReviewItem = Schemas["ReviewItem"];
export type AssetEdit = Schemas["AssetEditIn"];
export type ShotEdit = Schemas["ShotEditIn"];
export type BulkEdit = Schemas["BulkIn"];
export type Collection = Schemas["CollectionOut"];
export type GoldenField = Schemas["GoldenFieldOut"];
export type GoldenItem = Schemas["GoldenItem"];
export type GoldenTarget = Schemas["GoldenTarget"];
export type GoldenPage = Schemas["GoldenPage"];
export type GoldenStats = Schemas["GoldenStats"];
export type EvalRun = Schemas["EvalRunOut"];

/** Enum facets on AI attributes (single-valued URL filters). */
export const AI_ENUM_KEYS = [
  "shot_size",
  "camera_angle",
  "interior_exterior",
  "time_of_day",
  "person_identifiable",
  "review_state",
  "consent",
] as const;

/** Query of `GET /api/v1/assets` (the cursor is owned by the paging hook). */
export type AssetQuery = NonNullable<paths["/api/v1/assets"]["get"]["parameters"]["query"]>;
