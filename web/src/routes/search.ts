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
const SORTS: readonly SortKey[] = [
  "capture_desc",
  "capture_asc",
  "name_asc",
  "size_desc",
  "duration_desc",
];
const DATE = /^\d{4}-\d{2}-\d{2}$/;

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value : undefined;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T | undefined {
  return typeof value === "string" && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : undefined;
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
    sort: oneOf(raw.sort, SORTS),
    view: oneOf(raw.view, ["grid", "list"] as const),
  };
  // Drop undefined keys so the URL stays clean.
  return Object.fromEntries(Object.entries(result).filter(([, v]) => v !== undefined));
}

export type ProcessingTab = "queue" | "batches" | "workers" | "quarantine";
export type AdminTab = "users" | "workers" | "rules" | "settings" | "backups";

export function validateProcessingSearch(raw: Record<string, unknown>): { tab?: ProcessingTab } {
  const tab = oneOf(raw.tab, ["queue", "batches", "workers", "quarantine"] as const);
  return tab ? { tab } : {};
}

export function validateAdminSearch(raw: Record<string, unknown>): { tab?: AdminTab } {
  const tab = oneOf(raw.tab, ["users", "workers", "rules", "settings", "backups"] as const);
  return tab ? { tab } : {};
}

export function validateLoginSearch(raw: Record<string, unknown>): { redirect?: string } {
  const redirect = text(raw.redirect);
  // Only same-origin paths are accepted so a crafted link cannot bounce users elsewhere.
  return redirect && redirect.startsWith("/") && !redirect.startsWith("//") ? { redirect } : {};
}
