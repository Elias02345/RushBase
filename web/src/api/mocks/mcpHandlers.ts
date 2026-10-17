import { http, HttpResponse } from "msw";

import type { McpAuditEntry, McpClient, NeedDetail, NeedItem } from "../contracts";
import type { AiDeps } from "./aiHandlers";
import { buildAssets } from "./data";

/** Mock needs lists and MCP administration (same shapes as the server). */

interface McpState {
  needs: NeedDetail[];
  clients: McpClient[];
  audit: McpAuditEntry[];
  redirects: string[];
}

const CLIENT_ID = "0194b0f0-0000-7000-8000-000000000001";
/** First filed video of the mock library: it shows up as a candidate on its detail page. */
const MOCK_ASSET =
  buildAssets().find((a) => a.media_type === "video" && a.status === "filed")?.id ?? "";

function fresh(): McpState {
  const item = (id: string, description: string, over: Partial<NeedItem> = {}): NeedItem => ({
    id,
    description,
    filters: {},
    count_wanted: 1,
    status: "open",
    confirmed: 0,
    candidates: [],
    ...over,
  });
  return {
    needs: [
      {
        id: "0194b0f0-0000-7000-8000-0000000000a1",
        title: "Imagefilm Hafen",
        purpose: "Kurzer Film über den Hafen im Herbst",
        status: "open",
        created_by: "mcp",
        created_at: "2026-10-12T09:30:00Z",
        items: [
          item("0194b0f0-0000-7000-8000-0000000000b1", "Fischerboote bei Sonnenaufgang", {
            filters: { media_type: "video" },
            candidates: [
              {
                id: "0194b0f0-0000-7000-8000-0000000000c1",
                asset_id: "0194b0f0-0000-7000-8000-0000000000d1",
                shot_idx: null,
                score: 0.0492,
                status: "candidate",
                source: "auto",
                asset: null,
              },
            ],
          }),
          item("0194b0f0-0000-7000-8000-0000000000b2", "Drohnenflug über der Mole", {
            count_wanted: 2,
          }),
        ],
      },
      {
        id: "0194b0f0-0000-7000-8000-0000000000a2",
        title: "Messe-Teaser",
        purpose: "Zehn Sekunden für die Messe",
        status: "open",
        created_by: "user",
        created_at: "2026-10-13T10:00:00Z",
        items: [
          item("0194b0f0-0000-7000-8000-0000000000b3", "Kran im Gegenlicht", {
            candidates: [
              {
                id: "0194b0f0-0000-7000-8000-0000000000c2",
                asset_id: MOCK_ASSET,
                shot_idx: null,
                score: 0.0511,
                status: "candidate",
                source: "auto",
                asset: null,
              },
            ],
          }),
        ],
      },
    ],
    clients: [
      {
        id: CLIENT_ID,
        name: "Claude",
        kind: "dcr",
        client_id: "rbclient_demo",
        scopes: ["needs", "read"],
        grants: 1,
        created_at: "2026-10-10T08:00:00Z",
        last_used_at: "2026-10-15T17:20:00Z",
        revoked_at: null,
      },
    ],
    audit: [
      {
        id: "0194b0f0-0000-7000-8000-0000000000e1",
        at: "2026-10-15T17:20:00Z",
        client_name: "Claude",
        client_id: CLIENT_ID,
        tool: "search_media",
        duration_ms: 84,
        result_chars: 4210,
        ok: true,
        error: null,
        args_hash: "0".repeat(64),
      },
    ],
    redirects: [],
  };
}

let state = fresh();

export function resetMcpState(): void {
  state = fresh();
}

function summary(need: NeedDetail) {
  const counts: Record<string, number> = {};
  for (const item of need.items) counts[item.status] = (counts[item.status] ?? 0) + 1;
  return {
    id: need.id,
    title: need.title,
    purpose: need.purpose,
    status: need.status,
    created_at: need.created_at,
    items: counts,
    candidates: need.items.reduce(
      (n, i) => n + i.candidates.filter((c) => c.status !== "rejected").length,
      0,
    ),
  };
}

export function mcpHandlers({ guarded }: Pick<AiDeps, "guarded">) {
  const find = (id: string | readonly string[] | undefined) => state.needs.find((n) => n.id === id);
  return [
    http.get(
      "/api/v1/needs",
      guarded("viewer", ({ request }) => {
        const status = new URL(request.url).searchParams.get("status");
        const rows = state.needs.filter((n) => !status || n.status === status);
        return HttpResponse.json({ items: rows.map(summary), next_offset: null });
      }),
    ),
    http.post(
      "/api/v1/needs",
      guarded("editor", async ({ request }) => {
        const body = (await request.json()) as {
          title: string;
          purpose?: string;
          items?: { description: string }[];
        };
        const need: NeedDetail = {
          id: `0194b0f0-0000-7000-8000-${String(state.needs.length + 10).padStart(12, "0")}`,
          title: body.title,
          purpose: body.purpose ?? "",
          status: "open",
          created_by: "user",
          created_at: new Date().toISOString(),
          items: (body.items ?? []).map((item, index) => ({
            id: `0194b0f0-0000-7000-8000-${String(state.needs.length * 100 + index + 500).padStart(12, "0")}`,
            description: item.description,
            filters: {},
            count_wanted: 1,
            status: "open",
            confirmed: 0,
            candidates: [],
          })),
        };
        state.needs.unshift(need);
        return HttpResponse.json(need, { status: 201 });
      }),
    ),
    http.get(
      "/api/v1/needs/for-asset/:asset",
      guarded("viewer", ({ params }) =>
        HttpResponse.json(
          state.needs.flatMap((need) =>
            need.items.flatMap((item) =>
              item.candidates
                .filter((c) => c.asset_id === params.asset)
                .map((c) => ({
                  list_id: need.id,
                  list_title: need.title,
                  list_status: need.status,
                  item_id: item.id,
                  item_description: item.description,
                  item_status: item.status,
                  count_wanted: item.count_wanted,
                  candidate_id: c.id,
                  status: c.status,
                  source: c.source,
                  score: c.score,
                  shot_idx: c.shot_idx,
                })),
            ),
          ),
        ),
      ),
    ),
    http.get(
      "/api/v1/needs/:id",
      guarded("viewer", ({ params }) => {
        const need = find(params.id);
        return need
          ? HttpResponse.json(need)
          : HttpResponse.json({ detail: "needs list not found" }, { status: 404 });
      }),
    ),
    http.patch(
      "/api/v1/needs/items/:id",
      guarded("editor", async ({ request, params }) => {
        const body = (await request.json()) as { status?: NeedItem["status"] };
        for (const need of state.needs)
          for (const item of need.items)
            if (item.id === params.id && body.status) item.status = body.status;
        return HttpResponse.json({ id: params.id, status: body.status ?? "open" });
      }),
    ),
    http.patch(
      "/api/v1/needs/:id",
      guarded("editor", async ({ request, params }) => {
        const need = find(params.id);
        if (!need) return HttpResponse.json({ detail: "needs list not found" }, { status: 404 });
        Object.assign(need, (await request.json()) as Partial<NeedDetail>);
        return HttpResponse.json(need);
      }),
    ),
    http.put(
      "/api/v1/needs/items/:item/candidates/:asset",
      guarded("editor", async ({ request, params }) => {
        const body = (await request.json()) as { status: "candidate" | "confirmed" | "rejected" };
        for (const need of state.needs)
          for (const item of need.items)
            for (const candidate of item.candidates)
              if (item.id === params.item && candidate.asset_id === params.asset) {
                candidate.status = body.status;
                item.confirmed = item.candidates.filter((c) => c.status === "confirmed").length;
                if (item.confirmed >= item.count_wanted) item.status = "fulfilled";
              }
        return HttpResponse.json({ id: params.asset, status: body.status, item_status: "open" });
      }),
    ),
    http.get(
      "/api/v1/admin/mcp",
      guarded("admin", () =>
        HttpResponse.json({
          enabled: true,
          public_url: "https://media.example.org",
          mcp_url: "https://media.example.org/mcp",
          warnings: [],
          rate_limit_per_minute: 120,
          extra_redirect_uris: state.redirects,
          tools: 26,
          clients: state.clients,
        }),
      ),
    ),
    http.post(
      "/api/v1/admin/mcp/clients/:id/revoke",
      guarded("admin", ({ params }) => {
        for (const client of state.clients)
          if (client.id === params.id) {
            client.revoked_at = new Date().toISOString();
            client.grants = 0;
          }
        return new HttpResponse(null, { status: 204 });
      }),
    ),
    http.put(
      "/api/v1/admin/mcp/redirect-uris",
      guarded("admin", async ({ request }) => {
        const body = (await request.json()) as { uris: string[] };
        state.redirects = body.uris;
        return HttpResponse.json(body.uris);
      }),
    ),
    http.get(
      "/api/v1/admin/mcp/audit",
      guarded("admin", ({ request }) => {
        const tool = new URL(request.url).searchParams.get("tool");
        const items = state.audit.filter((a) => !tool || a.tool === tool);
        return HttpResponse.json({
          items,
          next_offset: null,
          tools: [...new Set(state.audit.map((a) => a.tool))],
        });
      }),
    ),
  ];
}
