import { delay, http, HttpResponse } from "msw";

import type {
  AssetQuery,
  AssetSummary,
  EnrollmentTokenInfo,
  FilingPreviewItem,
  FilingRulesDoc,
  PathMapping,
  StatsResponse,
} from "../contracts";
import type { Batch, User } from "../types";
import {
  batchIdForLabel,
  buildAssets,
  buildBatches,
  buildDetail,
  buildFacets,
  buildStatus,
  buildUsers,
  buildWorkers,
  defaultFilingRules,
  defaultPathMapping,
} from "./data";
import { keyframeSvg, posterSvg, spriteSvg } from "./svg";

/** Password every mock account accepts. */
export const MOCK_PASSWORD = "rushbase-demo-pass";
const SESSION_KEY = "rushbase.mock.user";
const SCENARIO_KEY = "rushbase.mock.scenario";

/** `default`, `fresh` (setup wizard not run yet) or `no-workers` (nothing online). */
export type Scenario = "default" | "fresh" | "no-workers";

function scenario(): Scenario {
  try {
    const value = window.sessionStorage.getItem(SCENARIO_KEY);
    if (value === "fresh" || value === "no-workers") return value;
  } catch {
    // Storage may be unavailable; the default scenario applies.
  }
  return "default";
}

function sessionUser(): string | null {
  try {
    return window.sessionStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}

function setSessionUser(username: string | null): void {
  try {
    if (username) window.sessionStorage.setItem(SESSION_KEY, username);
    else window.sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // Without storage the mock session simply does not survive a reload.
  }
}

interface MockState {
  assets: AssetSummary[];
  users: User[];
  batches: Batch[];
  tokens: EnrollmentTokenInfo[];
  mapping: PathMapping;
  rules: FilingRulesDoc;
  paused: boolean;
  setupDone: boolean;
  backups: { name: string; size: number; created_at: string; kind: string }[];
}

function freshState(): MockState {
  return {
    assets: buildAssets(),
    users: buildUsers(),
    batches: buildBatches(),
    tokens: [
      {
        id: "t0000000-0000-4000-8000-000000000001",
        note: "Studio PC",
        created_at: "2026-10-01T09:00:00Z",
        expires_at: "2026-10-02T09:00:00Z",
        used_at: "2026-10-01T09:12:00Z",
        revoked_at: null,
      },
    ],
    mapping: defaultPathMapping(),
    rules: defaultFilingRules(),
    paused: false,
    setupDone: scenario() !== "fresh",
    backups: [
      {
        name: "rushbase-2026-10-12-0300.tar.zst",
        size: 48_200_000,
        created_at: "2026-10-12T03:00:00Z",
        kind: "daily",
      },
      {
        name: "rushbase-2026-10-05-0300.tar.zst",
        size: 47_100_000,
        created_at: "2026-10-05T03:00:00Z",
        kind: "weekly",
      },
    ],
  };
}

let state = freshState();

/** Reset the in-memory mock server (tests call this between cases). */
export function resetMockState(): void {
  state = freshState();
}

function unauthorized() {
  return HttpResponse.json({ detail: "authentication required" }, { status: 401 });
}

function currentUser(): User | null {
  const name = sessionUser();
  return state.users.find((user) => user.username === name && !user.disabled) ?? null;
}

type Role = User["role"];
const RANK: Record<Role, number> = { viewer: 0, editor: 1, admin: 2 };

interface Ctx {
  user: User;
  request: Request;
  params: Record<string, string | readonly string[] | undefined>;
}

/** Run `respond` for signed-in users with at least `role`; 401/403 otherwise (like the server). */
function guarded(role: Role, respond: (ctx: Ctx) => Response | Promise<Response>) {
  return async ({ request, params }: { request: Request; params: Ctx["params"] }) => {
    const user = currentUser();
    if (!user) return unauthorized();
    if (RANK[user.role] < RANK[role]) {
      return HttpResponse.json({ detail: `${role} role required` }, { status: 403 });
    }
    await delay(60);
    return respond({ user, request, params });
  };
}

function matches(asset: AssetSummary, query: Record<string, string>): boolean {
  const equals = (key: keyof AssetSummary, value: string | undefined) =>
    value === undefined || value === "" || String(asset[key]) === value;
  if (!equals("media_type", query.media_type)) return false;
  if (!equals("status", query.status)) return false;
  if (!equals("camera_model", query.camera_model)) return false;
  if (!equals("lens_model", query.lens_model)) return false;
  if (!equals("log_profile", query.log_profile)) return false;
  if (!equals("orientation", query.orientation)) return false;
  if (query.batch_id && batchIdForLabel(asset.batch_label) !== query.batch_id) return false;
  const day = asset.capture_at?.slice(0, 10) ?? "";
  if (query.date_from && day < query.date_from) return false;
  if (query.date_to && day > query.date_to) return false;
  if (query.q) {
    const haystack = [
      asset.title,
      asset.filename,
      asset.camera_model,
      asset.lens_model,
      asset.batch_label,
      asset.codec,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    const terms = query.q.toLowerCase().split(/\s+/);
    if (!terms.every((term) => haystack.includes(term))) return false;
  }
  return true;
}

function sortAssets(items: AssetSummary[], sort: AssetQuery["sort"]): AssetSummary[] {
  const copy = [...items];
  const time = (a: AssetSummary) => (a.capture_at ? Date.parse(a.capture_at) : 0);
  switch (sort) {
    case "capture_asc":
      return copy.sort((a, b) => time(a) - time(b));
    case "name_asc":
      return copy.sort((a, b) => (a.title ?? a.filename).localeCompare(b.title ?? b.filename));
    case "size_desc":
      return copy.sort((a, b) => b.size_total - a.size_total);
    case "duration_desc":
      return copy.sort((a, b) => (b.duration_ms ?? 0) - (a.duration_ms ?? 0));
    default:
      return copy.sort((a, b) => time(b) - time(a));
  }
}

function expand(template: string, asset: AssetSummary): string {
  const date = asset.capture_at ?? "";
  return template
    .replaceAll("{year}", date.slice(0, 4) || "0000")
    .replaceAll("{month}", date.slice(5, 7) || "00")
    .replaceAll("{day}", date.slice(8, 10) || "00")
    .replaceAll("{camera}", asset.camera_model?.replaceAll(" ", "_") ?? "Unknown")
    .replaceAll("{type}", asset.media_type)
    .replaceAll("{batch}", asset.batch_label?.replaceAll(" ", "_") ?? "Loose");
}

function previewRules(doc: FilingRulesDoc) {
  const candidates = state.assets.filter((a) => a.status === "processing" || a.status === "review");
  const items: FilingPreviewItem[] = [];
  let unmatched = 0;
  for (const asset of candidates.slice(0, 25)) {
    const rule = doc.rules.find(
      (r) =>
        r.enabled &&
        (!r.when.media_type || r.when.media_type === asset.media_type) &&
        (!r.when.camera_model || r.when.camera_model === asset.camera_model) &&
        (!r.when.batch_contains || (asset.batch_label ?? "").includes(r.when.batch_contains)),
    );
    if (!rule) unmatched += 1;
    items.push({
      asset_id: asset.id,
      filename: asset.filename,
      from_rel_path: `Input/${asset.batch_label ?? "Loose"}/${asset.filename}`,
      to_rel_path: `Content/${expand(rule?.target ?? doc.fallback, asset)}/${asset.filename}`,
      rule_id: rule?.id ?? null,
    });
  }
  return { items, unmatched };
}

function svg(body: string) {
  return new HttpResponse(body, {
    headers: { "content-type": "image/svg+xml", "cache-control": "max-age=3600" },
  });
}

function setBatch(id: Ctx["params"][string], patch: Partial<Batch>): Response {
  const batch = state.batches.find((candidate) => candidate.id === id);
  if (!batch) return HttpResponse.json({ detail: "batch not found" }, { status: 404 });
  Object.assign(batch, patch);
  return new HttpResponse(null, { status: 204 });
}

function storageStats(): StatsResponse {
  const byType = new Map<string, { bytes: number; count: number }>();
  const byYear = new Map<number, { bytes: number; count: number }>();
  let total = 0;
  for (const asset of state.assets) {
    total += asset.size_total;
    const type = byType.get(asset.media_type) ?? { bytes: 0, count: 0 };
    byType.set(asset.media_type, { bytes: type.bytes + asset.size_total, count: type.count + 1 });
    const year = Number(asset.capture_at?.slice(0, 4) ?? 0);
    const entry = byYear.get(year) ?? { bytes: 0, count: 0 };
    byYear.set(year, { bytes: entry.bytes + asset.size_total, count: entry.count + 1 });
  }
  const duplicates = state.assets.filter((asset) => asset.status === "duplicate");
  return {
    storage: {
      total_bytes: total,
      by_media_type: [...byType].map(([key, v]) => ({ key, ...v })),
      by_year: [...byYear].sort((a, b) => a[0] - b[0]).map(([year, v]) => ({ year, ...v })),
    },
    duplicates: {
      count: duplicates.length,
      wasted_bytes: duplicates.reduce((n, a) => n + a.size_total, 0),
    },
    backlog: { queued: 41, leased: 3 },
  };
}

export const handlers = [
  http.get("/api/health", () =>
    HttpResponse.json({
      status: "ok",
      version: "0.1.0",
      database: true,
      library_mounted: state.setupDone ? true : null,
      lease_held: state.setupDone ? true : null,
      setup_required: !state.setupDone,
    }),
  ),

  http.get("/api/v1/setup", () =>
    HttpResponse.json({
      needs_setup: !state.setupDone,
      library_configured: true,
      library_initialised: false,
      restore_available: !state.setupDone && state.backups.length > 0,
      backups: state.setupDone ? [] : state.backups,
    }),
  ),
  http.post("/api/v1/setup", async ({ request }) => {
    if (state.setupDone) {
      return HttpResponse.json({ detail: "setup is already complete" }, { status: 409 });
    }
    const body = (await request.json()) as {
      username: string;
      display_name?: string;
      locale: "de" | "en";
    };
    const user: User = {
      id: "u0000000-0000-4000-8000-0000000000aa",
      username: body.username,
      display_name: body.display_name || body.username,
      role: "admin",
      locale: body.locale,
      disabled: false,
      last_login_at: new Date().toISOString(),
    };
    state.users = [user];
    state.setupDone = true;
    setSessionUser(user.username);
    await delay(250);
    return HttpResponse.json({ user, csrf_token: "mock-csrf" }, { status: 201 });
  }),
  http.post("/api/v1/setup/restore", async () => {
    await delay(500);
    state.setupDone = true;
    return HttpResponse.json({ ok: true });
  }),

  http.post("/api/v1/auth/login", async ({ request }) => {
    const body = (await request.json()) as { username: string; password: string };
    await delay(150);
    const user = state.users.find((candidate) => candidate.username === body.username.trim());
    if (!user || user.disabled || body.password !== MOCK_PASSWORD) {
      return HttpResponse.json({ detail: "invalid credentials" }, { status: 401 });
    }
    setSessionUser(user.username);
    return HttpResponse.json({ user, csrf_token: "mock-csrf" });
  }),
  http.post("/api/v1/auth/logout", () => {
    setSessionUser(null);
    return new HttpResponse(null, { status: 204 });
  }),
  http.get(
    "/api/v1/auth/me",
    guarded("viewer", ({ user }) => HttpResponse.json(user)),
  ),

  http.get(
    "/api/v1/status",
    guarded("viewer", () => {
      const online = scenario() === "no-workers" ? 0 : 1;
      return HttpResponse.json(buildStatus(online, state.paused));
    }),
  ),

  http.get(
    "/api/v1/assets",
    guarded("viewer", ({ request }) => {
      const query = Object.fromEntries(new URL(request.url).searchParams) as Record<string, string>;
      const sorted = sortAssets(
        state.assets.filter((asset) => matches(asset, query)),
        query.sort as AssetQuery["sort"],
      );
      const offset = query.cursor ? Number(atob(query.cursor)) : 0;
      const limit = Math.min(Number(query.limit) || 60, 200);
      const items = sorted.slice(offset, offset + limit);
      const next = offset + limit < sorted.length ? btoa(String(offset + limit)) : null;
      return HttpResponse.json({ items, next_cursor: next, total: sorted.length });
    }),
  ),
  http.get(
    "/api/v1/assets/:id",
    guarded("viewer", ({ params }) => {
      const asset = state.assets.find((candidate) => candidate.id === params.id);
      if (!asset) return HttpResponse.json({ detail: "asset not found" }, { status: 404 });
      return HttpResponse.json(buildDetail(asset, state.mapping));
    }),
  ),
  http.post(
    "/api/v1/assets/:id/retry",
    guarded("editor", ({ params }) => {
      const asset = state.assets.find((candidate) => candidate.id === params.id);
      if (!asset) return HttpResponse.json({ detail: "asset not found" }, { status: 404 });
      asset.status = "processing";
      return new HttpResponse(null, { status: 202 });
    }),
  ),
  http.get(
    "/api/v1/facets",
    guarded("viewer", () => HttpResponse.json({ facets: buildFacets(state.assets) })),
  ),
  http.get(
    "/api/v1/stats",
    guarded("viewer", () => HttpResponse.json(storageStats())),
  ),

  http.get(
    "/api/v1/admin/workers",
    guarded("admin", () => HttpResponse.json(buildWorkers(scenario() !== "no-workers"))),
  ),
  http.post(
    "/api/v1/admin/workers/:id/revoke",
    guarded("admin", () => new HttpResponse(null, { status: 204 })),
  ),
  http.get(
    "/api/v1/admin/batches",
    guarded("viewer", () => HttpResponse.json(state.batches)),
  ),
  http.post(
    "/api/v1/admin/batches/:id/pause",
    guarded("editor", ({ params }) => setBatch(params.id, { paused: true })),
  ),
  http.post(
    "/api/v1/admin/batches/:id/resume",
    guarded("editor", ({ params }) => setBatch(params.id, { paused: false })),
  ),
  http.put(
    "/api/v1/admin/batches/:id/priority",
    guarded("editor", async ({ params, request }) => {
      const body = (await request.json()) as { priority: number };
      return setBatch(params.id, { priority: body.priority });
    }),
  ),
  http.post(
    "/api/v1/admin/queue/pause",
    guarded("admin", () => {
      state.paused = true;
      return new HttpResponse(null, { status: 204 });
    }),
  ),
  http.post(
    "/api/v1/admin/queue/resume",
    guarded("admin", () => {
      state.paused = false;
      return new HttpResponse(null, { status: 204 });
    }),
  ),

  http.get(
    "/api/v1/admin/users",
    guarded("admin", () => HttpResponse.json(state.users)),
  ),
  http.post(
    "/api/v1/admin/users",
    guarded("admin", async ({ request }) => {
      const body = (await request.json()) as Pick<
        User,
        "username" | "display_name" | "role" | "locale"
      >;
      if (state.users.some((user) => user.username === body.username)) {
        return HttpResponse.json({ detail: "username already exists" }, { status: 409 });
      }
      const user: User = {
        id: `u0000000-0000-4000-8000-${String(state.users.length + 1).padStart(12, "0")}`,
        username: body.username,
        display_name: body.display_name || body.username,
        role: body.role,
        locale: body.locale,
        disabled: false,
        last_login_at: null,
      };
      state.users.push(user);
      return HttpResponse.json(user, { status: 201 });
    }),
  ),
  http.patch(
    "/api/v1/admin/users/:id",
    guarded("admin", async ({ params, request }) => {
      const user = state.users.find((candidate) => candidate.id === params.id);
      if (!user) return HttpResponse.json({ detail: "user not found" }, { status: 404 });
      const body = (await request.json()) as Partial<Pick<User, "role" | "disabled">>;
      Object.assign(user, body);
      return HttpResponse.json(user);
    }),
  ),

  http.post(
    "/api/v1/admin/enrollment-tokens",
    guarded("admin", async ({ request }) => {
      const body = (await request.json().catch(() => ({}))) as { note?: string };
      const id = `t0000000-0000-4000-8000-${String(state.tokens.length + 1).padStart(12, "0")}`;
      const now = Date.now();
      const info: EnrollmentTokenInfo = {
        id,
        note: body.note ?? "",
        created_at: new Date(now).toISOString(),
        expires_at: new Date(now + 24 * 3_600_000).toISOString(),
        used_at: null,
        revoked_at: null,
      };
      state.tokens.unshift(info);
      const token = `rbe_${btoa(id).replaceAll("=", "").slice(0, 40)}mockOnlyToken`;
      return HttpResponse.json({ id, token, expires_at: info.expires_at }, { status: 201 });
    }),
  ),
  http.get(
    "/api/v1/admin/enrollment-tokens",
    guarded("admin", () => HttpResponse.json(state.tokens)),
  ),
  http.post(
    "/api/v1/admin/enrollment-tokens/:id/revoke",
    guarded("admin", ({ params }) => {
      const token = state.tokens.find((candidate) => candidate.id === params.id);
      if (token) token.revoked_at = new Date().toISOString();
      return new HttpResponse(null, { status: 204 });
    }),
  ),

  http.get(
    "/api/v1/admin/backups",
    guarded("admin", () => HttpResponse.json(state.backups)),
  ),
  http.post(
    "/api/v1/admin/backups",
    guarded("admin", async () => {
      await delay(600);
      const stamp = new Date().toISOString();
      const info = {
        name: `rushbase-${stamp.slice(0, 10)}-${stamp.slice(11, 16).replace(":", "")}.tar.zst`,
        size: 48_900_000,
        created_at: stamp,
        kind: "manual",
      };
      state.backups.unshift(info);
      return HttpResponse.json(info, { status: 201 });
    }),
  ),

  http.get(
    "/api/v1/admin/settings/path-mapping",
    guarded("admin", () => HttpResponse.json(state.mapping)),
  ),
  http.put(
    "/api/v1/admin/settings/path-mapping",
    guarded("admin", async ({ request }) => {
      state.mapping = (await request.json()) as PathMapping;
      return HttpResponse.json(state.mapping);
    }),
  ),
  http.get(
    "/api/v1/admin/filing-rules",
    guarded("admin", () => HttpResponse.json(state.rules)),
  ),
  http.put(
    "/api/v1/admin/filing-rules",
    guarded("admin", async ({ request }) => {
      state.rules = (await request.json()) as FilingRulesDoc;
      return HttpResponse.json(state.rules);
    }),
  ),
  http.post(
    "/api/v1/admin/filing-rules/preview",
    guarded("admin", async ({ request }) => {
      await delay(250);
      return HttpResponse.json(previewRules((await request.json()) as FilingRulesDoc));
    }),
  ),

  http.get("/mock/media/poster/:file", ({ params }) =>
    svg(posterSvg(String(params.file).replace(".svg", ""))),
  ),
  http.get("/mock/media/sprite/:file", ({ params }) =>
    svg(spriteSvg(String(params.file).replace(".svg", ""), 5, 4, 20)),
  ),
  http.get("/mock/media/keyframe/:file", ({ params }) => {
    const name = String(params.file).replace(".svg", "");
    const cut = name.lastIndexOf("-");
    return svg(keyframeSvg(name.slice(0, cut), Number(name.slice(cut + 1))));
  }),
];
