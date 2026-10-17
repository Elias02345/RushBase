import { http, HttpResponse } from "msw";

import type { AssetDetail, AssetSummary } from "../contracts";
import type { AiDeps } from "./aiHandlers";
import { buildBatches } from "./data";

/** Mock trash, privacy lock, sync suggestions and content migration. */

interface Trashed {
  asset: AssetSummary;
  prior_status: string;
  trashed_at: string;
}

interface SafetyState {
  trash: Trashed[];
  migration: boolean;
  /** Decisions on the mock sync suggestions, keyed by audio asset id. */
  sync: Map<string, string>;
}

let state: SafetyState = { trash: [], migration: false, sync: new Map() };

export function resetSafetyState(): void {
  state = { trash: [], migration: false, sync: new Map() };
}

const SYNC_PREFIX = "sync-";

/**
 * Every audio asset gets one suggestion with the first video of its batch; the suggestion id is
 * derived from the audio asset id.
 */
export function syncFor(asset: AssetSummary, assets: AssetSummary[]): AssetDetail["sync"] {
  const make = (audio: AssetSummary, video: AssetSummary, other: AssetSummary, role: string) => ({
    id: SYNC_PREFIX + audio.id,
    other_asset_id: other.id,
    other_filename: other.filename,
    other_role: role,
    offset_ms: 5000,
    confidence: 0.92,
    basis: "timecode",
    status: state.sync.get(audio.id) ?? "suggested",
    video_id: video.id,
  });
  if (asset.media_type === "audio") {
    const video = assets.find(
      (a) => a.media_type === "video" && a.batch_label === asset.batch_label,
    );
    return video ? [make(asset, video, video, "video")] : [];
  }
  if (asset.media_type !== "video") return [];
  const audio = assets.find((a) => a.media_type === "audio" && a.batch_label === asset.batch_label);
  const first = assets.find((a) => a.media_type === "video" && a.batch_label === asset.batch_label);
  return audio && first?.id === asset.id ? [make(audio, asset, audio, "audio")] : [];
}

export function safetyHandlers(deps: AiDeps) {
  const { guarded } = deps;
  const days = () => 30;
  return [
    http.post(
      "/api/v1/assets/:id/trash",
      guarded("admin", ({ params }) => {
        const assets = deps.assets();
        const index = assets.findIndex((a) => a.id === params.id);
        const asset = assets[index];
        if (!asset) return HttpResponse.json({ detail: "asset not found" }, { status: 404 });
        assets.splice(index, 1);
        state.trash.push({
          asset: { ...asset, status: "trashed" },
          prior_status: asset.status,
          trashed_at: new Date().toISOString(),
        });
        return new HttpResponse(null, { status: 204 });
      }),
    ),
    http.get(
      "/api/v1/admin/trash",
      guarded("admin", () =>
        HttpResponse.json({
          retention_days: days(),
          items: state.trash.map((t) => ({
            asset_id: t.asset.id,
            filename: t.asset.filename,
            media_type: t.asset.media_type,
            size_total: t.asset.size_total,
            prior_status: t.prior_status,
            trashed_at: t.trashed_at,
            expires_at: new Date(Date.parse(t.trashed_at) + days() * 86_400_000).toISOString(),
            files: 1,
          })),
        }),
      ),
    ),
    http.post(
      "/api/v1/admin/trash/:id/restore",
      guarded("admin", ({ params }) => {
        const index = state.trash.findIndex((t) => t.asset.id === params.id);
        const item = state.trash[index];
        if (!item)
          return HttpResponse.json({ detail: "asset is not in the trash" }, { status: 409 });
        state.trash.splice(index, 1);
        deps.assets().push({ ...item.asset, status: item.prior_status });
        return new HttpResponse(null, { status: 204 });
      }),
    ),
    http.post(
      "/api/v1/privacy/lock",
      guarded("editor", async ({ request }) => {
        const body = (await request.json()) as {
          batch_id?: string;
          person?: string;
          dry_run?: boolean;
        };
        const label = buildBatches().find((b) => b.id === body.batch_id)?.label;
        const needle = (body.person ?? "").toLowerCase();
        const matches = deps
          .assets()
          .filter((a) =>
            body.batch_id
              ? a.batch_label === label
              : needle !== "" && (a.title ?? "").toLowerCase().includes(needle),
          );
        if (body.dry_run === false) for (const a of matches) a.locked = true;
        return HttpResponse.json({
          matched: matches.length,
          updated: body.dry_run === false ? matches.length : 0,
          sample: matches.slice(0, 20).map((a) => a.id),
        });
      }),
    ),
    http.post(
      "/api/v1/sync-suggestions/:id",
      guarded("editor", async ({ params, request }) => {
        const id = String(params.id);
        if (!id.startsWith(SYNC_PREFIX)) {
          return HttpResponse.json({ detail: "suggestion not found" }, { status: 404 });
        }
        state.sync.set(
          id.slice(SYNC_PREFIX.length),
          ((await request.json()) as { decision: string }).decision,
        );
        return new HttpResponse(null, { status: 204 });
      }),
    ),
    http.get(
      "/api/v1/admin/migration/settings",
      guarded("admin", () => HttpResponse.json({ enabled: state.migration })),
    ),
    http.put(
      "/api/v1/admin/migration/settings",
      guarded("admin", async ({ request }) => {
        state.migration = ((await request.json()) as { enabled: boolean }).enabled;
        return HttpResponse.json({ enabled: state.migration });
      }),
    ),
    http.post(
      "/api/v1/admin/migration/preview",
      guarded("admin", async ({ request }) => {
        if (!state.migration) {
          return HttpResponse.json({ detail: "the migration tool is disabled" }, { status: 403 });
        }
        const body = (await request.json()) as { batch_id?: string; template: string };
        const label = buildBatches().find((b) => b.id === body.batch_id)?.label;
        const members = deps
          .assets()
          .filter((a) => a.batch_label === label)
          .slice(0, 3);
        return HttpResponse.json({
          items: members.map((a) => ({
            asset_ids: [a.id],
            filename: a.filename,
            old_dir: `Content/${a.media_type}/2026/${label}`,
            new_dir: body.template.replace(/\{[^}]*\}/g, "x").replace(/\/$/, ""),
            warnings: [],
            skipped: null,
          })),
          to_move: members.length,
          confirmation: `MIGRATE ${members.length}`,
        });
      }),
    ),
    http.post(
      "/api/v1/admin/migration/execute",
      guarded("admin", async ({ request }) => {
        const body = (await request.json()) as { confirmation: string };
        if (!body.confirmation.startsWith("MIGRATE ")) {
          return HttpResponse.json({ detail: "confirmation does not match" }, { status: 422 });
        }
        return HttpResponse.json({
          moved: Number(body.confirmation.slice(8)),
          failed: 0,
          relink_csv: "asset_id,old_rel,new_rel\n",
          relink_path: ".rushbase/journal/relink-mock.csv",
        });
      }),
    ),
  ];
}
