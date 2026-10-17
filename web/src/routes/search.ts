import { SORT_KEYS } from "../api/contracts";
import type { AssetStatus, MediaType, Orientation, SortKey } from "../api/contracts";

/** Typed, URL-synced library filters. Unknown or malformed values are dropped, never thrown. */
export interface LibrarySearch {
  q?: string;
  media_type?: MediaType;
  status?: AssetStatus;
  camera_model?: string;
  lens_model?: string;
  log_profile?: string;
  orientation?: Orientation;
  date_from?: string;
  date_to?: string;
  batch_id?: string;
  shot_size?: string;
  camera_angle?: string;
  interior_exterior?: string;
  time_of_day?: string;
  person_identifiable?: string;
  review_state?: string;
  consent?: string;
  collection_id?: string;
  /** Taxonomy filters as `field:value`. */
  tag?: string[];
  /** Search result granularity: whole assets or scenes (shots). */
  level?: "asset" | "shot";
  sort?: SortKey;
  view?: "grid" | "list";
}

const MEDIA: readonly MediaType[] = ["video", "photo", "audio", "other"];
const STATUSES: readonly AssetStatus[] = [
  "ingesting",
  "processing",
  "review",
  "filed",
  "quarantined",
  "trashed",
  "duplicate",
];
const ORIENTATIONS: readonly Orientation[] = ["landscape", "portrait", "square"];
const DATE = /^\d{4}-\d{2}-\d{2}$/;

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value : undefined;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T | undefined {
  return typeof value === "string" && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : undefined;
}

function strings(value: unknown): string[] | undefined {
  const list = (Array.isArray(value) ? value : [value]).filter(
    (item): item is string => typeof item === "string" && item.includes(":"),
  );
  return list.length > 0 ? list : undefined;
}

function date(value: unknown): string | undefined {
  return typeof value === "string" && DATE.test(value) ? value : undefined;
}

export function validateLibrarySearch(raw: Record<string, unknown>): LibrarySearch {
  const result: LibrarySearch = {
    q: text(raw.q),
    media_type: oneOf(raw.media_type, MEDIA),
    status: oneOf(raw.status, STATUSES),
    camera_model: text(raw.camera_model),
    lens_model: text(raw.lens_model),
    log_profile: text(raw.log_profile),
    orientation: oneOf(raw.orientation, ORIENTATIONS),
    date_from: date(raw.date_from),
    date_to: date(raw.date_to),
    batch_id: text(raw.batch_id),
    shot_size: text(raw.shot_size),
    camera_angle: text(raw.camera_angle),
    interior_exterior: text(raw.interior_exterior),
    time_of_day: text(raw.time_of_day),
    person_identifiable: text(raw.person_identifiable),
    review_state: text(raw.review_state),
    consent: text(raw.consent),
    collection_id: text(raw.collection_id),
    tag: strings(raw.tag),
    level: oneOf(raw.level, ["asset", "shot"] as const),
    sort: oneOf<SortKey>(raw.sort, SORT_KEYS),
    view: oneOf(raw.view, ["grid", "list"] as const),
  };
  // Drop undefined keys so the URL stays clean.
  return Object.fromEntries(Object.entries(result).filter(([, v]) => v !== undefined));
}

export type ProcessingTab = "queue" | "batches" | "workers" | "quarantine";
export type AdminTab =
  "users" | "workers" | "rules" | "settings" | "backups" | "search" | "mcp" | "trash" | "migration";

export function validateProcessingSearch(raw: Record<string, unknown>): { tab?: ProcessingTab } {
  const tab = oneOf(raw.tab, ["queue", "batches", "workers", "quarantine"] as const);
  return tab ? { tab } : {};
}

export function validateAdminSearch(raw: Record<string, unknown>): { tab?: AdminTab } {
  const tab = oneOf(raw.tab, [
    "users",
    "workers",
    "rules",
    "settings",
    "backups",
    "search",
    "mcp",
    "trash",
    "migration",
  ] as const);
  return tab ? { tab } : {};
}

export function validateNeedsSearch(raw: Record<string, unknown>): {
  list?: string;
  state?: "open" | "closed";
} {
  const list = text(raw.list);
  const state = oneOf(raw.state, ["open", "closed"] as const);
  return { ...(list ? { list } : {}), ...(state ? { state } : {}) };
}

export function validateLoginSearch(raw: Record<string, unknown>): { redirect?: string } {
  const redirect = text(raw.redirect);
  // Only same-origin paths are accepted so a crafted link cannot bounce users elsewhere.
  return redirect && redirect.startsWith("/") && !redirect.startsWith("//") ? { redirect } : {};
}

/** The filters and query that narrow facet counts: everything except order and view. */
export function facetQuery(search: LibrarySearch): Omit<LibrarySearch, "sort" | "view" | "level"> {
  return Object.fromEntries(
    Object.entries(search).filter(([key]) => !["sort", "view", "level"].includes(key)),
  );
}

export function validateAssetSearch(raw: Record<string, unknown>): { t?: number } {
  const value = Number(raw.t);
  return Number.isFinite(value) && value > 0 ? { t: Math.round(value) } : {};
}
