import { http, HttpResponse } from "msw";

import type {
  AssetDetail,
  AssetSummary,
  Collection,
  EvalRun,
  Facet,
  ReviewItem,
  SearchHit,
  Taxonomy,
  TaxonomyValue,
} from "../contracts";
import type { User } from "../types";
import { buildDetail, type PathRoots } from "./data";
import {
  buildGoldenFields,
  buildProposals,
  buildTaxonomy,
  buildTranscript,
  hitFor,
  reviewReasons,
  withAi,
} from "./ai";

type Role = User["role"];
type Guard = (
  role: Role,
  respond: (ctx: {
    user: User;
    request: Request;
    params: Record<string, string | readonly string[] | undefined>;
  }) => Response | Promise<Response>,
) => Parameters<typeof http.get>[1];

interface AiState {
  taxonomy: Taxonomy;
  labels: Map<string, string>;
  collections: Collection[];
  evalRuns: EvalRun[];
  /** Tags added/removed by edits: asset id to `field:value` list. */
  editedTags: Map<string, { add: string[]; remove: string[] }>;
}

function freshAiState(): AiState {
  return {
    taxonomy: buildTaxonomy(),
    labels: new Map(),
    collections: [],
    evalRuns: [],
    editedTags: new Map(),
  };
}

let ai = freshAiState();

export function resetAiState(): void {
  ai = freshAiState();
}

export interface AiDeps {
  guarded: Guard;
  assets: () => AssetSummary[];
  roots: () => PathRoots;
}

function query(request: Request): Record<string, string> {
  return Object.fromEntries(new URL(request.url).searchParams);
}

function allValues(): TaxonomyValue[] {
  return ai.taxonomy.fields.flatMap((f) => f.values);
}

function findValue(id: unknown): TaxonomyValue | undefined {
  return allValues().find((v) => v.id === id);
}

/** Detail with the mock AI section and the user's tag edits applied. */
export function detailFor(asset: AssetSummary, deps: AiDeps): AssetDetail {
  const index = deps.assets().indexOf(asset);
  const detail = withAi(buildDetail(asset, deps.roots()), ai.taxonomy, index);
  const edits = ai.editedTags.get(asset.id);
  if (edits) {
    detail.ai.tags = detail.ai.tags.filter(
      (t) => !edits.remove.includes(`${t.field_key}:${t.value}`),
    );
    for (const item of edits.add) {
      const [fieldKey = "", name = ""] = item.split(":");
      const field = ai.taxonomy.fields.find((f) => f.key === fieldKey);
      const value = field?.values.find((v) => v.value === name);
      if (
        field &&
        value &&
        !detail.ai.tags.some((t) => t.field_key === fieldKey && t.value === name)
      ) {
        detail.ai.tags.push({
          id: `h-${asset.id}-${item}`,
          field_key: field.key,
          field_label_de: field.label_de,
          field_label_en: field.label_en,
          value: value.value,
          label_de: value.label_de,
          label_en: value.label_en,
          source: "human",
          confidence: 1,
          shot_idx: null,
        });
      }
    }
  }
  return detail;
}

/** Facets for AI attributes and taxonomy fields, appended to the technical facets. */
export function extraFacets(assets: AssetSummary[]): Facet[] {
  const states = new Map<string, number>();
  for (const asset of assets) {
    if (asset.review_state !== "none") {
      states.set(asset.review_state, (states.get(asset.review_state) ?? 0) + 1);
    }
  }
  const facets: Facet[] = [];
  if (states.size > 0) {
    facets.push({
      key: "review_state",
      label_de: "Prüfstatus",
      label_en: "Review state",
      kind: "enum",
      values: [...states].map(([value, count]) => ({ value, count })),
    });
  }
  const location = ai.taxonomy.fields[0];
  if (location) {
    facets.push({
      key: `tag.${location.key}`,
      label_de: location.label_de,
      label_en: location.label_en,
      kind: "enum",
      values: location.values
        .filter((v) => v.status === "approved")
        .map((v, i) => ({
          value: `${location.key}:${v.value}`,
          count: Math.max(1, Math.floor(assets.length / (i + 3))),
        })),
    });
  }
  return facets;
}

function searchMatches(asset: AssetSummary, text: string, detail: AssetDetail): boolean {
  const hay = [
    asset.title,
    asset.filename,
    asset.summary,
    detail.ai.summary_short_de,
    detail.ai.summary_short_en,
    ...detail.shots.map(
      (s) => `${s.ai?.description_short_de ?? ""} ${s.ai?.description_short_en ?? ""}`,
    ),
    ...detail.ai.tags.map((t) => `${t.value} ${t.label_de ?? ""}`),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return text
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((term) => hay.includes(term));
}

export function aiHandlers(deps: AiDeps) {
  const { guarded } = deps;
  const page = <T>(items: T[], q: Record<string, string>) => {
    const offset = Number(q.offset) || 0;
    const limit = Number(q.limit) || 30;
    return {
      slice: items.slice(offset, offset + limit),
      next: offset + limit < items.length ? offset + limit : null,
    };
  };
  const analysed = () => deps.assets().filter((a) => a.status === "filed" || a.status === "review");

  return [
    http.get(
      "/api/v1/search",
      guarded("viewer", ({ request }) => {
        const q = query(request);
        const level = q.level === "shot" ? "shot" : "asset";
        const hits: SearchHit[] = [];
        for (const asset of analysed()) {
          if (q.media_type && asset.media_type !== q.media_type) continue;
          const detail = detailFor(asset, deps);
          if (!searchMatches(asset, q.q ?? "", detail)) continue;
          if (level === "asset") hits.push(hitFor(asset, null));
          else for (const shot of detail.shots.slice(0, 2)) hits.push(hitFor(asset, shot));
        }
        const { slice, next } = page(hits, q);
        return HttpResponse.json({
          items: slice,
          level,
          offset: Number(q.offset) || 0,
          next_offset: next,
          candidates: hits.length,
          channels_ms: { fulltext: 4.2, text: 11.5, image: 14.1 },
          skipped: {},
        });
      }),
    ),
    http.get(
      "/api/v1/search/capabilities",
      guarded("viewer", () =>
        HttpResponse.json({
          fulltext: true,
          text_semantic: {
            available: true,
            model_id: "multilingual-e5-base",
            revision: null,
            reason: null,
            vectors_compatible: 480,
            vectors_mismatched: 0,
          },
          image_semantic: {
            available: true,
            model_id: "siglip2-base-patch16-256",
            revision: null,
            reason: null,
            vectors_compatible: 910,
            vectors_mismatched: 0,
          },
          search_by_image: false,
          reembed_needed: false,
        }),
      ),
    ),
    http.get(
      "/api/v1/assets/:id/similar",
      guarded("viewer", ({ params }) => {
        const others = analysed()
          .filter((a) => a.id !== params.id)
          .slice(0, 8);
        return HttpResponse.json({
          items: others.map((a) => hitFor(a, null)),
          level: "asset",
          offset: 0,
          next_offset: null,
          candidates: others.length,
          channels_ms: { image: 6.3 },
          skipped: {},
        });
      }),
    ),
    http.get(
      "/api/v1/shots/:id/similar",
      guarded("viewer", () => {
        const items = analysed()
          .filter((a) => a.media_type === "video")
          .slice(0, 6)
          .map((a) => hitFor(a, detailFor(a, deps).shots[0] ?? null));
        return HttpResponse.json({
          items,
          level: "shot",
          offset: 0,
          next_offset: null,
          candidates: items.length,
          channels_ms: { image: 5.1 },
          skipped: {},
        });
      }),
    ),
    http.get(
      "/api/v1/assets/:id/transcript",
      guarded("viewer", ({ params, request }) => {
        const asset = deps.assets().find((a) => a.id === params.id);
        const q = (query(request).q ?? "").toLowerCase();
        const full = buildTranscript(asset?.duration_ms ?? 60_000);
        return HttpResponse.json({
          segments: full.segments.filter((s) => !q || s.text.toLowerCase().includes(q)),
          events: full.events,
          total_segments: full.segments.length,
        });
      }),
    ),

    http.get(
      "/api/v1/taxonomy",
      guarded("viewer", () => HttpResponse.json(ai.taxonomy)),
    ),
    http.get(
      "/api/v1/taxonomy/proposals",
      guarded("editor", () => HttpResponse.json(buildProposals(ai.taxonomy))),
    ),
    http.post(
      "/api/v1/taxonomy/values/:id/approve",
      guarded("editor", ({ params }) => {
        const value = findValue(params.id);
        if (!value) return HttpResponse.json({ detail: "value not found" }, { status: 404 });
        value.status = "approved";
        ai.taxonomy.version += 1;
        return HttpResponse.json(value);
      }),
    ),
    http.post(
      "/api/v1/taxonomy/values/:id/deprecate",
      guarded("admin", ({ params }) => {
        const value = findValue(params.id);
        if (!value) return HttpResponse.json({ detail: "value not found" }, { status: 404 });
        value.status = "deprecated";
        ai.taxonomy.version += 1;
        return HttpResponse.json(value);
      }),
    ),
    http.post(
      "/api/v1/taxonomy/values/:id/merge",
      guarded("admin", async ({ params, request }) => {
        const body = (await request.json()) as { target_id: string };
        const source = findValue(params.id);
        const target = findValue(body.target_id);
        if (!source || !target)
          return HttpResponse.json({ detail: "value not found" }, { status: 404 });
        target.synonyms = [...target.synonyms, source.value];
        for (const field of ai.taxonomy.fields)
          field.values = field.values.filter((v) => v.id !== source.id);
        ai.taxonomy.version += 1;
        return HttpResponse.json(target);
      }),
    ),
    http.patch(
      "/api/v1/taxonomy/values/:id",
      guarded("admin", async ({ params, request }) => {
        const body = (await request.json()) as { value?: string };
        const value = findValue(params.id);
        if (!value) return HttpResponse.json({ detail: "value not found" }, { status: 404 });
        if (body.value) {
          value.synonyms = [...value.synonyms, value.value];
          value.value = body.value;
        }
        return HttpResponse.json(value);
      }),
    ),
    http.post(
      "/api/v1/taxonomy/values/:id/synonyms",
      guarded("admin", async ({ params, request }) => {
        const body = (await request.json()) as { synonym: string };
        const value = findValue(params.id);
        if (!value) return HttpResponse.json({ detail: "value not found" }, { status: 404 });
        value.synonyms = [...value.synonyms, body.synonym];
        return HttpResponse.json(value);
      }),
    ),
    http.post(
      "/api/v1/taxonomy/fields",
      guarded("admin", async ({ request }) => {
        const body = (await request.json()) as {
          key: string;
          label_de: string;
          label_en: string;
          kind: "single" | "multi";
        };
        const field = { id: `f-${body.key}`, description: "", sort: 9, values: [], ...body };
        ai.taxonomy.fields.push(field);
        return HttpResponse.json(field, { status: 201 });
      }),
    ),
    http.post(
      "/api/v1/taxonomy/fields/:id/values",
      guarded("editor", async ({ params, request, user }) => {
        const body = (await request.json()) as { value: string };
        const field = ai.taxonomy.fields.find((f) => f.id === params.id);
        if (!field) return HttpResponse.json({ detail: "field not found" }, { status: 404 });
        const value: TaxonomyValue = {
          id: `v-${field.key}-${body.value}`,
          field_id: field.id,
          value: body.value,
          label_de: null,
          label_en: null,
          synonyms: [],
          parent_id: null,
          status: user.role === "admin" ? "approved" : "proposed",
          description: "",
          proposal_count: 0,
          asset_count: 0,
        };
        field.values.push(value);
        return HttpResponse.json(value, { status: 201 });
      }),
    ),

    http.get(
      "/api/v1/review",
      guarded("editor", ({ request }) => {
        const q = query(request);
        const pending = deps.assets().filter((a) => a.review_state === "pending");
        const items: ReviewItem[] = pending
          .map((asset, i) => ({ asset, reasons: reviewReasons(i) }))
          .filter((item) => !q.review_reason || item.reasons.includes(q.review_reason));
        return HttpResponse.json({ items, next_cursor: null, total: items.length });
      }),
    ),
    http.patch(
      "/api/v1/assets/:id",
      guarded("editor", async ({ params, request }) => {
        const asset = deps.assets().find((a) => a.id === params.id);
        if (!asset) return HttpResponse.json({ detail: "asset not found" }, { status: 404 });
        const body = (await request.json()) as {
          review_done?: boolean;
          locked?: boolean;
          add_tags?: { field: string; value: string }[];
          remove_tags?: { field: string; value: string }[];
        };
        if (body.review_done) asset.review_state = "done";
        if (body.locked !== undefined) asset.locked = body.locked;
        const edits = ai.editedTags.get(asset.id) ?? { add: [], remove: [] };
        for (const t of body.add_tags ?? []) edits.add.push(`${t.field}:${t.value}`);
        for (const t of body.remove_tags ?? []) edits.remove.push(`${t.field}:${t.value}`);
        ai.editedTags.set(asset.id, edits);
        return HttpResponse.json({ changed: {}, tags_added: [], tags_removed: [] });
      }),
    ),
    http.patch(
      "/api/v1/shots/:id",
      guarded("editor", () => HttpResponse.json({ changed: {}, tags_added: [], tags_removed: [] })),
    ),
    http.post(
      "/api/v1/assets/bulk",
      guarded("editor", async ({ request }) => {
        const body = (await request.json()) as { asset_ids: string[]; locked?: boolean };
        for (const asset of deps.assets()) {
          if (body.asset_ids.includes(asset.id) && body.locked !== undefined)
            asset.locked = body.locked;
        }
        return HttpResponse.json({ updated: body.asset_ids.length });
      }),
    ),
    http.get(
      "/api/v1/collections",
      guarded("viewer", () => HttpResponse.json(ai.collections)),
    ),
    http.post(
      "/api/v1/collections",
      guarded("editor", async ({ request }) => {
        const body = (await request.json()) as { name: string };
        const collection = {
          id: `c-${ai.collections.length + 1}`,
          name: body.name,
          assets: 0,
          created_at: new Date().toISOString(),
        };
        ai.collections.push(collection);
        return HttpResponse.json(collection, { status: 201 });
      }),
    ),

    http.get(
      "/api/v1/golden/fields",
      guarded("viewer", () => HttpResponse.json(buildGoldenFields(ai.taxonomy))),
    ),
    http.get(
      "/api/v1/golden/items",
      guarded("editor", ({ request }) => {
        const q = query(request);
        const labelled = new Set([...ai.labels.keys()].map((k) => k.split("|")[0]));
        const candidates = analysed().filter(
          (a) => a.media_type !== "audio" && !labelled.has(a.id),
        );
        const skip = Number(q.offset) || 0;
        const items = candidates.slice(skip, skip + 1).map((asset) => {
          const detail = detailFor(asset, deps);
          const targets =
            detail.shots.length > 0
              ? detail.shots.map((shot) => ({
                  shot_id: shot.id,
                  idx: shot.idx,
                  start_ms: shot.start_ms,
                  end_ms: shot.end_ms,
                  keyframe_urls: shot.keyframes.map((k) => k.url ?? "").filter(Boolean),
                  ai: {
                    shot_size: shot.ai?.shot_size ?? null,
                    camera_angle: shot.ai?.camera_angle ?? null,
                    interior_exterior: shot.ai?.interior_exterior ?? null,
                    time_of_day: shot.ai?.time_of_day ?? null,
                    people_count: String(shot.ai?.people_count ?? ""),
                    person_identifiable: shot.ai?.person_identifiable ?? null,
                  },
                  labels: {},
                }))
              : [
                  {
                    shot_id: null,
                    idx: null,
                    start_ms: null,
                    end_ms: null,
                    keyframe_urls: asset.poster_url ? [asset.poster_url] : [],
                    ai: { shot_size: detail.ai.shot_size, camera_angle: detail.ai.camera_angle },
                    labels: {},
                  },
                ];
          return { asset, targets };
        });
        return HttpResponse.json({ items, total: candidates.length });
      }),
    ),
    http.put(
      "/api/v1/golden/labels",
      guarded("editor", async ({ request }) => {
        const body = (await request.json()) as {
          asset_id: string;
          shot_id: string | null;
          field: string;
          expected: string;
        };
        ai.labels.set(`${body.asset_id}|${body.shot_id ?? ""}|${body.field}`, body.expected);
        return new HttpResponse(null, { status: 204 });
      }),
    ),
    http.get(
      "/api/v1/golden/stats",
      guarded("viewer", () =>
        HttpResponse.json({
          labels: ai.labels.size,
          assets: new Set([...ai.labels.keys()].map((k) => k.split("|")[0])).size,
          corrections: 0,
          by_field: {},
        }),
      ),
    ),
    http.get(
      "/api/v1/eval/runs",
      guarded("viewer", () => HttpResponse.json(ai.evalRuns)),
    ),
    http.post(
      "/api/v1/admin/eval",
      guarded("admin", () => {
        const run: EvalRun = {
          id: `e-${ai.evalRuns.length + 1}`,
          created_at: new Date().toISOString(),
          label: null,
          golden_count: ai.labels.size,
          model_refs: { vision: ["qwen3.5-4b-q8_0"] },
          metrics: { overall: { n: ai.labels.size, accuracy: 0.82 }, fields: {} },
        };
        ai.evalRuns.unshift(run);
        return HttpResponse.json(run);
      }),
    ),
    http.post(
      "/api/v1/admin/reembed",
      guarded("admin", () => HttpResponse.json({ assets: 0 })),
    ),
  ];
}
