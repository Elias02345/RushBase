import { http, HttpResponse } from "msw";

import type {
  AssetSummary,
  AssetUsage,
  AuditLogEntry,
  ByModelResult,
  Facet,
  GrowthMonth,
  HeatDimension,
  Heatmap,
  ModelUse,
  PersonalToken,
  Throughput,
} from "../contracts";
import type { AiDeps } from "./aiHandlers";
import { buildTaxonomy } from "./ai";

/** Mock usage feedback, personal tokens, reprocess-by-model, dashboard data and the audit log. */

const NOW = Date.parse("2026-10-16T12:00:00Z");
const HOUR = 3_600_000;
const DAY = 24 * HOUR;

interface InsightState {
  usage: Map<string, AssetUsage[]>;
  tokens: PersonalToken[];
  models: ModelUse[];
  seeded: boolean;
}

function freshState(): InsightState {
  const model = (
    stage: ModelUse["stage"],
    id: string,
    version: string,
    profile: string | null,
    assets: number,
  ): ModelUse => ({
    stage,
    model_id: id,
    model_version: version,
    profile,
    assets,
  });
  return {
    usage: new Map(),
    tokens: [
      {
        id: "p0000000-0000-4000-8000-000000000001",
        name: "Schnittplatz",
        prefix: "rbp_a1b2",
        scopes: [],
        last_used_at: new Date(NOW - 3 * HOUR).toISOString(),
        revoked_at: null,
        expires_at: new Date(NOW + 60 * DAY).toISOString(),
        created_at: new Date(NOW - 30 * DAY).toISOString(),
      },
      {
        id: "p0000000-0000-4000-8000-000000000002",
        name: "Skripte",
        prefix: "rbp_c3d4",
        scopes: ["read"],
        last_used_at: null,
        revoked_at: null,
        expires_at: null,
        created_at: new Date(NOW - 5 * DAY).toISOString(),
      },
    ],
    models: [
      model("vision", "qwen3.5-9b-q4_k_m", "1.2", "gpu8", 140),
      model("vision", "qwen3.5-9b-q4_k_m", "1.0", "gpu8", 52),
      model("classify", "qwen3.5-9b-q4_k_m", "1.2", "gpu8", 140),
      model("classify", "qwen3.5-9b-q4_k_m", "1.0", "cpu", 52),
      model("embed", "siglip2-base-patch16-256", "2025-06", null, 192),
      model("audio", "whisper-large-v3", "3.0", "gpu8", 31),
    ],
    seeded: false,
  };
}

let state = freshState();

export function resetInsightState(): void {
  state = freshState();
}

/** Seed usage rows on first use: a few clips of the mock library are in two projects. */
function ensureUsage(assets: AssetSummary[]): void {
  if (state.seeded) return;
  state.seeded = true;
  const videos = assets.filter((a) => a.media_type === "video" && a.status === "filed");
  const row = (project: string, source: AssetUsage["source"], daysAgo: number): AssetUsage => ({
    project_name: project,
    source,
    machine: source === "resolve" ? "schnittplatz" : "",
    first_seen: new Date(NOW - (daysAgo + 3) * DAY).toISOString(),
    last_seen: new Date(NOW - daysAgo * DAY).toISOString(),
  });
  videos.slice(0, 6).forEach((asset, index) => {
    const rows = [row("Imagefilm Hafen", "resolve", 2)];
    if (index < 2) rows.push(row("Messe-Teaser", "manual", 9));
    state.usage.set(asset.id, rows);
  });
}

/** Usage rows of an asset for the detail view. */
export function usageFor(asset: AssetSummary, assets: AssetSummary[]): AssetUsage[] {
  ensureUsage(assets);
  return state.usage.get(asset.id) ?? [];
}

/** Does the asset match the `used_in_project` filter (`*` = any project)? */
export function usedIn(asset: AssetSummary, assets: AssetSummary[], project: string): boolean {
  const rows = usageFor(asset, assets);
  return project === "*" ? rows.length > 0 : rows.some((r) => r.project_name === project);
}

/** The "used in project" facet of the narrowed assets. */
export function usageFacet(narrowed: AssetSummary[], all: AssetSummary[]): Facet[] {
  ensureUsage(all);
  const counts = new Map<string, number>();
  for (const asset of narrowed) {
    for (const name of new Set((state.usage.get(asset.id) ?? []).map((r) => r.project_name))) {
      counts.set(name, (counts.get(name) ?? 0) + 1);
    }
  }
  if (counts.size === 0) return [];
  return [
    {
      key: "used_in_project",
      label_de: "Verwendet in Projekt",
      label_en: "Used in project",
      kind: "enum",
      values: [...counts].sort((a, b) => b[1] - a[1]).map(([value, count]) => ({ value, count })),
    },
  ];
}

// --- reprocess by model ---------------------------------------------------------------

function versionKey(version: string): (number | string)[] {
  return version.split(/[.-]/).map((part) => (/^\d+$/.test(part) ? Number(part) : part));
}

function versionLess(a: string, b: string): boolean {
  const left = versionKey(a);
  const right = versionKey(b);
  for (let i = 0; i < Math.max(left.length, right.length); i++) {
    const x = left[i] ?? 0;
    const y = right[i] ?? 0;
    if (x === y) continue;
    if (typeof x === "number" && typeof y === "number") return x < y;
    return String(x) < String(y);
  }
  return false;
}

function byModel(body: {
  stage: string;
  field: "model_version" | "model_id" | "profile";
  op: "lt" | "ne";
  value: string;
  dry_run: boolean;
}): ByModelResult {
  const groups = new Map<string | null, number>();
  for (const use of state.models) {
    if (use.stage !== body.stage) continue;
    const current = use[body.field];
    const hit = body.op === "lt" ? versionLess(current ?? "0", body.value) : current !== body.value;
    if (hit) groups.set(current, (groups.get(current) ?? 0) + use.assets);
  }
  const matched = [...groups.values()].reduce((n, c) => n + c, 0);
  return {
    dry_run: body.dry_run,
    matched,
    queued: body.dry_run ? 0 : matched,
    skipped: 0,
    truncated: false,
    by_value: [...groups].map(([value, assets]) => ({ value, assets })),
  };
}

// --- dashboard ------------------------------------------------------------------------

function growth(assets: AssetSummary[]): GrowthMonth[] {
  const months = new Map<string, GrowthMonth>();
  for (const asset of assets) {
    if (!asset.capture_at || asset.status === "trashed") continue;
    const key = asset.capture_at.slice(0, 7);
    const entry = months.get(key) ?? { month: key, bytes: 0, count: 0, by_media_type: {} };
    const bucket = entry.by_media_type[asset.media_type] ?? { bytes: 0, count: 0 };
    bucket.bytes += asset.size_total;
    bucket.count += 1;
    entry.by_media_type[asset.media_type] = bucket;
    entry.bytes += asset.size_total;
    entry.count += 1;
    months.set(key, entry);
  }
  return [...months.values()].sort((a, b) => a.month.localeCompare(b.month));
}

function throughput(): Throughput {
  const hours: Throughput["hours"] = [];
  const stages = ["probe", "preview", "vision", "classify"];
  let total = 0;
  for (let i = 0; i < 7 * 24 + 1; i++) {
    const at = new Date(NOW - (7 * 24 - i) * HOUR);
    const busy = i % 24 >= 9 && i % 24 <= 22 && i % 5 !== 0;
    const count = busy ? 8 + ((i * 7) % 23) : (i * 3) % 4;
    total += count;
    hours.push({
      hour: at.toISOString(),
      count,
      by_stage: count > 0 ? { [stages[i % stages.length] ?? "probe"]: count } : {},
    });
  }
  return {
    since: hours[0]?.hour ?? "",
    total,
    per_hour_avg: total / hours.length,
    hours,
  };
}

const TECHNICAL: HeatDimension[] = [
  { key: "media_type", label_de: "Medientyp", label_en: "Media type", kind: "technical" },
  { key: "orientation", label_de: "Ausrichtung", label_en: "Orientation", kind: "technical" },
  { key: "camera_model", label_de: "Kamera", label_en: "Camera", kind: "technical" },
];

function dimensions(): HeatDimension[] {
  const fields = buildTaxonomy().fields.map<HeatDimension>((f) => ({
    key: f.key,
    label_de: f.label_de,
    label_en: f.label_en,
    kind: "taxonomy",
  }));
  return [...TECHNICAL, ...fields];
}

function axisValues(key: string, assets: AssetSummary[]): string[] {
  const field = buildTaxonomy().fields.find((f) => f.key === key);
  if (field) return field.values.filter((v) => v.status === "approved").map((v) => v.value);
  const pick = (a: AssetSummary): string | null =>
    key === "media_type" ? a.media_type : key === "orientation" ? a.orientation : a.camera_model;
  return [...new Set(assets.map(pick).filter((v): v is string => Boolean(v)))].sort();
}

function heatmap(rows: string, columns: string, assets: AssetSummary[]): Heatmap {
  const rowValues = axisValues(rows, assets);
  const columnValues = axisValues(columns, assets);
  const technical = (key: string) => TECHNICAL.some((d) => d.key === key);
  const attribute = (a: AssetSummary, key: string) =>
    key === "media_type" ? a.media_type : key === "orientation" ? a.orientation : a.camera_model;
  const cells: Heatmap["cells"] = [];
  for (const [ri, row] of rowValues.entries()) {
    for (const [ci, col] of columnValues.entries()) {
      const count =
        technical(rows) && technical(columns)
          ? assets.filter((a) => attribute(a, rows) === row && attribute(a, columns) === col).length
          : (ri * 7 + ci * 5) % 4 === 0
            ? 0
            : ((ri + 2) * (ci + 3) * 3) % 41;
      if (count > 0) cells.push({ row, col, count });
    }
  }
  const counts = new Map(cells.map((c) => [`${c.row}\u0000${c.col}`, c.count]));
  const all = rowValues.flatMap((row) =>
    columnValues.map((col) => ({ row, col, count: counts.get(`${row}\u0000${col}`) ?? 0 })),
  );
  return {
    rows_dimension: rows,
    columns_dimension: columns,
    row_values: rowValues,
    column_values: columnValues,
    cells,
    max: Math.max(0, ...cells.map((c) => c.count)),
    truncated: false,
    weakest: all.sort((a, b) => a.count - b.count || a.row.localeCompare(b.row)).slice(0, 8),
  };
}

// --- audit log ------------------------------------------------------------------------

const ACTORS = [
  { kind: "user", id: "u0000000-0000-4000-8000-000000000001", name: "elias" },
  { kind: "user", id: "u0000000-0000-4000-8000-000000000002", name: "mara" },
  { kind: "worker", id: "w0000000-0000-4000-8000-000000000001", name: "gpu-pc" },
  { kind: "mcp", id: "0194b0f0-0000-7000-8000-000000000001", name: "Claude" },
  { kind: "system", id: null, name: null },
] as const;
const ACTIONS = [
  { action: "auth.login", target: null },
  { action: "api_token.create", target: "api_token" },
  { action: "queue.reprocess", target: null },
  { action: "asset.trash", target: "asset" },
  { action: "usage.report", target: "project" },
  { action: "filing.rule.activate", target: "filing_rule" },
  { action: "needs.create", target: "need_list" },
] as const;

function auditEntries(): AuditLogEntry[] {
  return Array.from({ length: 120 }, (_, i) => {
    const actor = ACTORS[(i * 3) % ACTORS.length] ?? ACTORS[0];
    const act = ACTIONS[(i * 5) % ACTIONS.length] ?? ACTIONS[0];
    return {
      id: `0194b0f0-0000-7000-8000-${String(900_000 - i).padStart(12, "0")}`,
      at: new Date(NOW - i * 47 * 60_000).toISOString(),
      actor_kind: actor.kind,
      actor_id: actor.id,
      actor_name: actor.name,
      action: act.action,
      target_kind: act.target,
      target_id: act.target ? `t-${1000 + i}` : null,
      details: { note: "mock entry", index: i },
      ip: actor.kind === "user" ? "192.0.2.10" : null,
    };
  });
}

export function insightHandlers({ guarded, assets }: Pick<AiDeps, "guarded" | "assets">) {
  return [
    http.get(
      "/api/v1/usage/projects",
      guarded("viewer", () => {
        ensureUsage(assets());
        const counts = new Map<string, number>();
        for (const rows of state.usage.values()) {
          for (const name of new Set(rows.map((r) => r.project_name))) {
            counts.set(name, (counts.get(name) ?? 0) + 1);
          }
        }
        return HttpResponse.json(
          [...counts].map(([project_name, count]) => ({ project_name, assets: count })),
        );
      }),
    ),
    http.put(
      "/api/v1/assets/:id/usage",
      guarded("editor", async ({ request, params }) => {
        ensureUsage(assets());
        const { project_name } = (await request.json()) as { project_name: string };
        const id = String(params.id);
        const rows = state.usage.get(id) ?? [];
        if (!rows.some((r) => r.project_name === project_name && r.source === "manual")) {
          const at = new Date().toISOString();
          rows.unshift({
            project_name,
            source: "manual",
            machine: "",
            first_seen: at,
            last_seen: at,
          });
        }
        state.usage.set(id, rows);
        return new HttpResponse(null, { status: 204 });
      }),
    ),
    http.delete(
      "/api/v1/assets/:id/usage",
      guarded("editor", ({ request, params }) => {
        ensureUsage(assets());
        const url = new URL(request.url);
        const id = String(params.id);
        const name = url.searchParams.get("project_name");
        const source = url.searchParams.get("source") ?? "manual";
        const rows = state.usage.get(id) ?? [];
        const kept = rows.filter((r) => !(r.project_name === name && r.source === source));
        if (kept.length === rows.length) {
          return HttpResponse.json({ detail: "usage not found" }, { status: 404 });
        }
        state.usage.set(id, kept);
        return new HttpResponse(null, { status: 204 });
      }),
    ),

    http.get(
      "/api/v1/tokens",
      guarded("viewer", () => HttpResponse.json(state.tokens)),
    ),
    http.post(
      "/api/v1/tokens",
      guarded("viewer", async ({ request }) => {
        const body = (await request.json()) as {
          name: string;
          scopes: string[];
          expires_in_days?: number | null;
        };
        const created: PersonalToken = {
          id: `p0000000-0000-4000-8000-${String(state.tokens.length + 1).padStart(12, "0")}`,
          name: body.name,
          prefix: "rbp_new0",
          scopes: body.scopes,
          last_used_at: null,
          revoked_at: null,
          expires_at: body.expires_in_days
            ? new Date(Date.now() + body.expires_in_days * DAY).toISOString()
            : null,
          created_at: new Date().toISOString(),
        };
        state.tokens.unshift(created);
        return HttpResponse.json(
          { ...created, token: "rbp_mock-token-shown-once-0123456789abcdef" },
          { status: 201 },
        );
      }),
    ),
    http.delete(
      "/api/v1/tokens/:id",
      guarded("viewer", ({ params }) => {
        const token = state.tokens.find((t) => t.id === params.id);
        if (!token) return HttpResponse.json({ detail: "token not found" }, { status: 404 });
        token.revoked_at = new Date().toISOString();
        return new HttpResponse(null, { status: 204 });
      }),
    ),

    http.get(
      "/api/v1/admin/reprocess/models",
      guarded("admin", () => HttpResponse.json(state.models)),
    ),
    http.post(
      "/api/v1/admin/reprocess/by-model",
      guarded("admin", async ({ request }) =>
        HttpResponse.json(byModel((await request.json()) as Parameters<typeof byModel>[0])),
      ),
    ),

    http.get(
      "/api/v1/stats/growth",
      guarded("viewer", () => HttpResponse.json(growth(assets()))),
    ),
    http.get(
      "/api/v1/stats/throughput",
      guarded("viewer", () => HttpResponse.json(throughput())),
    ),
    http.get(
      "/api/v1/stats/dimensions",
      guarded("viewer", () => HttpResponse.json(dimensions())),
    ),
    http.get(
      "/api/v1/stats/coverage",
      guarded("viewer", ({ request }) => {
        const q = new URL(request.url).searchParams;
        const known = dimensions().map((d) => d.key);
        const rows = q.get("rows") ?? "";
        const columns = q.get("columns") ?? "";
        if (!known.includes(rows) || !known.includes(columns)) {
          return HttpResponse.json({ detail: `unknown dimension` }, { status: 422 });
        }
        return HttpResponse.json(heatmap(rows, columns, assets()));
      }),
    ),

    http.get(
      "/api/v1/admin/audit/actions",
      guarded("admin", () => HttpResponse.json(ACTIONS.map((a) => a.action).sort())),
    ),
    http.get(
      "/api/v1/admin/audit",
      guarded("admin", ({ request }) => {
        const q = new URL(request.url).searchParams;
        const prefix = q.get("action");
        const rows = auditEntries().filter(
          (e) =>
            (!q.get("actor_kind") || e.actor_kind === q.get("actor_kind")) &&
            (!q.get("actor_id") || e.actor_id === q.get("actor_id")) &&
            (!prefix ||
              (prefix.endsWith("*")
                ? e.action.startsWith(prefix.slice(0, -1))
                : e.action === prefix)) &&
            (!q.get("target_kind") || e.target_kind === q.get("target_kind")) &&
            (!q.get("target_id") || e.target_id === q.get("target_id")) &&
            (!q.get("since") || e.at >= (q.get("since") ?? "")) &&
            (!q.get("until") || e.at < (q.get("until") ?? "")),
        );
        const offset = q.get("cursor") ? Number(atob(q.get("cursor") ?? "")) : 0;
        const limit = Number(q.get("limit")) || 50;
        const items = rows.slice(offset, offset + limit);
        const next = offset + limit < rows.length ? btoa(String(offset + limit)) : null;
        return HttpResponse.json({ items, next_cursor: next });
      }),
    ),
  ];
}
