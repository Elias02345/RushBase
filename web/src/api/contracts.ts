/**
 * Hand-written contracts for endpoints that are not in the generated OpenAPI client yet.
 *
 * The shapes follow the M3 read-API specification exactly. Once the server publishes them in
 * OpenAPI and `pnpm gen:api` has run, swap this file for the generated types: keep the exported
 * names, replace the bodies with aliases of `components["schemas"][...]` and make
 * `ContractPaths` an alias of the generated `paths`. Nothing else in the web app imports
 * request shapes from anywhere else, so that is a one-file change.
 *
 * Endpoints marked PROPOSED have no server implementation yet; the UI uses them behind the same
 * client and the mock layer answers them (see docs/STATUS.md for the list).
 */

export type MediaType = "video" | "photo" | "audio" | "other";
export type AssetStatus =
  "ingesting" | "processing" | "review" | "filed" | "quarantined" | "trashed" | "duplicate";
export type Orientation = "landscape" | "portrait" | "square";
export type SortKey = "capture_desc" | "capture_asc" | "name_asc" | "size_desc" | "duration_desc";

/** Contact-sheet layout of `sprite_url` (frames run left to right, top to bottom). */
export interface SpriteTiles {
  cols: number;
  rows: number;
  count: number;
}

export interface AssetSummary {
  id: string;
  media_type: MediaType;
  status: AssetStatus;
  title: string | null;
  filename: string;
  capture_at: string | null;
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  codec: string | null;
  camera_model: string | null;
  lens_model: string | null;
  log_profile: string | null;
  orientation: Orientation | null;
  size_total: number;
  poster_url: string | null;
  sprite_url: string | null;
  sprite_tiles: SpriteTiles | null;
  batch_label: string | null;
}

export interface AssetQuery {
  cursor?: string;
  limit?: number;
  media_type?: MediaType;
  status?: AssetStatus;
  batch_id?: string;
  camera_model?: string;
  lens_model?: string;
  log_profile?: string;
  orientation?: Orientation;
  date_from?: string;
  date_to?: string;
  q?: string;
  sort?: SortKey;
}

export interface AssetPage {
  items: AssetSummary[];
  next_cursor: string | null;
  total: number | null;
}

export interface AssetFile {
  role: string;
  rel_path: string;
  size: number;
  blake3: string | null;
  paths: { windows?: string; macos?: string; linux?: string };
}

export interface Keyframe {
  t_ms: number;
  url: string;
  position: number;
}

export interface Shot {
  idx: number;
  start_ms: number;
  end_ms: number;
  keyframes: Keyframe[];
  camera_motion: string | null;
  sharpness: number | null;
  shake: number | null;
}

export interface HistoryEntry {
  at: string;
  action: string;
  details: Record<string, unknown>;
}

export interface AssetDetail extends AssetSummary {
  files: AssetFile[];
  metadata: { normalized: Record<string, unknown> };
  shots: Shot[];
  proxy_url: string | null;
  waveform_url: string | null;
  content_rel_dir: string | null;
  duplicate_of: string | null;
  history: HistoryEntry[];
}

export interface FacetValue {
  value: string;
  count: number;
}

export interface Facet {
  key: string;
  label_de: string;
  label_en: string;
  kind: "enum" | "range" | "date";
  values?: FacetValue[];
  min?: number | string;
  max?: number | string;
}

export interface FacetsResponse {
  facets: Facet[];
}

export interface StatsResponse {
  storage: {
    total_bytes: number;
    by_media_type: { key: string; bytes: number; count: number }[];
    by_year: { year: number; bytes: number; count: number }[];
  };
  duplicates: { count: number; wasted_bytes: number };
  backlog: Record<string, number>;
}

// --- PROPOSED endpoints (no server implementation yet) --------------------------------------

export interface PathMapping {
  windows: string | null;
  macos: string | null;
  linux: string | null;
}

export interface EnrollmentTokenInfo {
  id: string;
  note: string;
  created_at: string;
  expires_at: string;
  used_at: string | null;
  revoked_at: string | null;
}

export interface FilingRule {
  id: string;
  name: string;
  enabled: boolean;
  /** Empty fields match everything. */
  when: {
    media_type: MediaType | null;
    camera_model: string | null;
    batch_contains: string | null;
  };
  /** Path template below `Content/`, for example `{year}/{month}/{camera}`. */
  target: string;
}

export interface FilingRulesDoc {
  rules: FilingRule[];
  /** Template used when no rule matches. */
  fallback: string;
}

export interface FilingPreviewItem {
  asset_id: string;
  filename: string;
  from_rel_path: string;
  to_rel_path: string;
  rule_id: string | null;
}

export interface FilingPreview {
  items: FilingPreviewItem[];
  unmatched: number;
}

type Json<T> = { content: { "application/json": T } };

/** The subset of the OpenAPI `paths` shape that the web app needs from the contracts above. */
export interface ContractPaths {
  "/api/v1/assets": {
    get: {
      parameters: { query?: AssetQuery };
      responses: { 200: Json<AssetPage> };
    };
  };
  "/api/v1/assets/{id}": {
    get: {
      parameters: { path: { id: string } };
      responses: { 200: Json<AssetDetail> };
    };
  };
  "/api/v1/assets/{id}/retry": {
    post: {
      parameters: { path: { id: string } };
      responses: { 202: { content?: never } };
    };
  };
  "/api/v1/facets": {
    get: { responses: { 200: Json<FacetsResponse> } };
  };
  "/api/v1/stats": {
    get: { responses: { 200: Json<StatsResponse> } };
  };
  "/api/v1/admin/enrollment-tokens": {
    get: { responses: { 200: Json<EnrollmentTokenInfo[]> } };
  };
  "/api/v1/admin/enrollment-tokens/{id}/revoke": {
    post: {
      parameters: { path: { id: string } };
      responses: { 204: { content?: never } };
    };
  };
  "/api/v1/admin/settings/path-mapping": {
    get: { responses: { 200: Json<PathMapping> } };
    put: {
      requestBody: Json<PathMapping>;
      responses: { 200: Json<PathMapping> };
    };
  };
  "/api/v1/admin/filing-rules": {
    get: { responses: { 200: Json<FilingRulesDoc> } };
    put: {
      requestBody: Json<FilingRulesDoc>;
      responses: { 200: Json<FilingRulesDoc> };
    };
  };
  "/api/v1/admin/filing-rules/preview": {
    post: {
      requestBody: Json<FilingRulesDoc>;
      responses: { 200: Json<FilingPreview> };
    };
  };
  "/api/v1/setup/restore": {
    post: {
      requestBody: Json<{ backup_name: string }>;
      responses: { 200: Json<{ ok: boolean }> };
    };
  };
}
