/** Shapes of the worker CLI's `--json` documents (docs/cli-json.md) as the app uses them. */

export interface Envelope<T> {
  schema_version: number;
  command: string;
  ok: boolean;
  data: T;
}

export interface CliResult<T> {
  /** Process exit code (0 = success). */
  code: number;
  /** Parsed envelope when the CLI printed one. */
  envelope: Envelope<T | { error: string }> | null;
  stderr: string;
}

export interface StageBacklog {
  pending_jobs: number;
  pending_files: number;
  pending_bytes: number;
  pending_duration_ms: number;
}

export interface Backlog {
  stages: Record<string, StageBacklog>;
  workers_online: number;
  workers_total: number;
}

export interface Issue {
  code: "no_server" | "not_enrolled" | "server_unreachable" | "mount" | "models_missing";
  message: string;
}

export interface StageEstimate {
  pending_jobs: number;
  mean_job_ms: number;
  seconds: number;
  source: "history" | "default";
}

export interface Overview {
  server: { url: string | null; reachable: boolean; error: string | null };
  enrolled: boolean;
  worker: { worker_id: string; name: string; server_url: string } | null;
  backlog: Backlog | null;
  totals: { files: number; hours: number; jobs: number };
  estimate: {
    total_seconds: number;
    concurrency: number;
    stages: Record<string, StageEstimate>;
  } | null;
  hardware: {
    gpu: string | null;
    vram_total_mb: number | null;
    vram_free_mb: number | null;
    backend: string;
    profile: string;
    cpu: string;
    cores: number;
  };
  models: { needed: string[]; installed: string[]; missing: string[] };
  runnable_stages: string[];
  mount: { root: string | null; ok: boolean; error: string | null } | null;
  last_run: { finished_at: string; reason: string; completed: number; failed: number } | null;
  issues: Issue[];
  ready: boolean;
}

export interface GpuStats {
  name: string;
  temperature_c: number | null;
  power_w: number | null;
  power_limit_w: number | null;
  utilization_pct: number | null;
  memory_used_mb: number | null;
  memory_total_mb: number | null;
}

export interface PlatformInfo {
  os: string;
  wayland: boolean;
  appimage: boolean;
  tray: boolean;
  updater: boolean;
}

export interface Settings {
  server_url: string | null;
  library_root: string | null;
  cli_command: string | null;
  language: string | null;
}

/** One line of `rushbase process --json` (the envelope's `data`). */
export interface ProcessEvent {
  event: string;
  job_id?: string;
  stage?: string;
  asset_id?: string;
  duration_ms?: number;
  error?: string;
  reason?: string;
  completed?: number;
  failed?: number;
  released?: number;
  by_stage?: Record<string, number>;
  [key: string]: unknown;
}

export interface ModelEvent {
  event: string;
  id?: string;
  file?: string;
  bytes?: number;
  total?: number;
  message?: string;
  ok?: boolean;
  [key: string]: unknown;
}

export type StreamName = "process" | "models";

export interface ShotSummary {
  id: string;
  idx: number;
  start_ms: number;
  end_ms: number;
  keyframes: { t_ms: number; url: string | null; position: string }[];
  ai?: {
    description_short_de?: string | null;
    description_short_en?: string | null;
    shot_size?: string | null;
  } | null;
}

export interface AssetSummary {
  id: string;
  media_type: "video" | "photo" | "audio" | string;
  filename: string;
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  codec: string | null;
  poster_url: string | null;
  summary: string | null;
  review_state: string;
}

export interface AssetDetail extends AssetSummary {
  files: { role: string; rel_path: string; paths: Record<string, string> }[];
  shots: ShotSummary[];
  proxy_url: string | null;
  local_path: string | null;
  local_exists: boolean;
  ai: {
    summary_short_de: string | null;
    summary_short_en: string | null;
    tags: { value: string; label_de: string | null; label_en: string | null; field_key: string }[];
  };
}

export interface BrowsePage {
  items: AssetSummary[];
  next_cursor: string | null;
  total: number | null;
}

export interface SearchHit {
  asset: AssetSummary;
  shot: {
    idx: number;
    start_ms: number;
    end_ms: number;
    description_de?: string | null;
    description_en?: string | null;
  } | null;
  score: number;
}

export interface SearchPage {
  items: SearchHit[];
  candidates: number;
  next_offset: number | null;
}

export interface Facet {
  key: string;
  label_de: string;
  label_en: string;
  kind: "enum" | "range" | "date";
  values?: { value: string; count: number }[];
}

export interface Basket {
  count: number;
  items: {
    asset_id: string;
    filename: string;
    selected_shots: number[] | null;
    shots: number;
    duration_ms: number | null;
  }[];
}

export interface ResolveStatus {
  installed: boolean;
  reachable: boolean;
  studio: boolean;
  version: string | null;
  project: string | null;
  error: string | null;
}

export interface DragCheck {
  ok: boolean;
  reason: string | null;
}
