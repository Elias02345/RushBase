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

/** Query of `GET /api/v1/assets` (the cursor is owned by the paging hook). */
export type AssetQuery = NonNullable<paths["/api/v1/assets"]["get"]["parameters"]["query"]>;
