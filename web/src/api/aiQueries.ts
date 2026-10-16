/** Queries and mutations of the AI features: hybrid search, taxonomy, review, golden set. */
import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api, unwrap } from "./client";
import type {
  AssetEdit,
  BulkEdit,
  Collection,
  EvalRun,
  GoldenField,
  GoldenPage,
  GoldenStats,
  Proposal,
  ReviewPage,
  SearchCapabilities,
  SearchOut,
  ShotEdit,
  Taxonomy,
  Transcript,
} from "./contracts";
import type { FacetQuery } from "./queries";

export const aiKeys = {
  capabilities: ["search-capabilities"] as const,
  taxonomy: ["taxonomy"] as const,
  proposals: ["taxonomy-proposals"] as const,
  review: (query: object) => ["review", query] as const,
  transcript: (id: string, q: string) => ["transcript", id, q] as const,
  similar: (kind: string, id: string) => ["similar", kind, id] as const,
  golden: (query: object) => ["golden", query] as const,
  goldenFields: ["golden-fields"] as const,
  goldenStats: ["golden-stats"] as const,
  collections: ["collections"] as const,
  evalRuns: ["eval-runs"] as const,
};

/** Request bodies list defaulted fields as required in the generated types; callers send a subset. */
export type Patch<T> = Partial<T>;

export type HybridQuery = FacetQuery & { q: string; level: "asset" | "shot" };

/** Hybrid search (offset-paged); only enabled while a query text exists. */
export function useHybridSearch(query: HybridQuery, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: ["search", query] as const,
    initialPageParam: 0,
    queryFn: ({ pageParam }): Promise<SearchOut> =>
      unwrap(
        api.GET("/api/v1/search", {
          params: { query: { ...query, offset: pageParam, limit: 40 } },
        }),
      ),
    getNextPageParam: (last) => last.next_offset ?? undefined,
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useSearchCapabilities() {
  return useQuery({
    queryKey: aiKeys.capabilities,
    queryFn: (): Promise<SearchCapabilities> => unwrap(api.GET("/api/v1/search/capabilities")),
    staleTime: 60_000,
  });
}

export function useSimilar(kind: "asset" | "shot", id: string | null) {
  return useQuery({
    queryKey: aiKeys.similar(kind, id ?? ""),
    enabled: id !== null,
    queryFn: (): Promise<SearchOut> =>
      kind === "asset"
        ? unwrap(
            api.GET("/api/v1/assets/{asset_id}/similar", {
              params: { path: { asset_id: id ?? "" }, query: { limit: 24 } },
            }),
          )
        : unwrap(
            api.GET("/api/v1/shots/{shot_id}/similar", {
              params: { path: { shot_id: id ?? "" }, query: { limit: 24 } },
            }),
          ),
  });
}

export function useTranscript(id: string, q: string, enabled: boolean) {
  return useQuery({
    queryKey: aiKeys.transcript(id, q),
    enabled,
    queryFn: (): Promise<Transcript> =>
      unwrap(
        api.GET("/api/v1/assets/{asset_id}/transcript", {
          params: { path: { asset_id: id }, query: { q: q || undefined } },
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

export function useTaxonomy() {
  return useQuery({
    queryKey: aiKeys.taxonomy,
    queryFn: (): Promise<Taxonomy> => unwrap(api.GET("/api/v1/taxonomy")),
  });
}

export function useProposals(enabled: boolean) {
  return useQuery({
    queryKey: aiKeys.proposals,
    enabled,
    queryFn: (): Promise<Proposal[]> => unwrap(api.GET("/api/v1/taxonomy/proposals")),
  });
}

/** Taxonomy mutations; each refreshes the vocabulary, the proposals and the facets. */
export function useTaxonomyActions() {
  const client = useQueryClient();
  const refresh = () => {
    void client.invalidateQueries({ queryKey: aiKeys.taxonomy });
    void client.invalidateQueries({ queryKey: aiKeys.proposals });
    void client.invalidateQueries({ queryKey: ["facets"] });
  };
  const approve = useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST("/api/v1/taxonomy/values/{value_id}/approve", {
          params: { path: { value_id: id } },
        }),
      ),
    onSuccess: refresh,
  });
  const deprecate = useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST("/api/v1/taxonomy/values/{value_id}/deprecate", {
          params: { path: { value_id: id } },
        }),
      ),
    onSuccess: refresh,
  });
  const merge = useMutation({
    mutationFn: ({ id, target }: { id: string; target: string }) =>
      unwrap(
        api.POST("/api/v1/taxonomy/values/{value_id}/merge", {
          params: { path: { value_id: id } },
          body: { target_id: target },
        }),
      ),
    onSuccess: refresh,
  });
  const rename = useMutation({
    mutationFn: ({ id, value }: { id: string; value: string }) =>
      unwrap(
        api.PATCH("/api/v1/taxonomy/values/{value_id}", {
          params: { path: { value_id: id } },
          body: { value },
        }),
      ),
    onSuccess: refresh,
  });
  const synonym = useMutation({
    mutationFn: ({ id, word }: { id: string; word: string }) =>
      unwrap(
        api.POST("/api/v1/taxonomy/values/{value_id}/synonyms", {
          params: { path: { value_id: id } },
          body: { synonym: word },
        }),
      ),
    onSuccess: refresh,
  });
  const createField = useMutation({
    mutationFn: (body: {
      key: string;
      label_de: string;
      label_en: string;
      kind: "single" | "multi";
    }) =>
      unwrap(
        api.POST("/api/v1/taxonomy/fields", {
          body: { description: "", sort: 0, ...body },
        }),
      ),
    onSuccess: refresh,
  });
  const createValue = useMutation({
    mutationFn: ({ fieldId, value }: { fieldId: string; value: string }) =>
      unwrap(
        api.POST("/api/v1/taxonomy/fields/{field_id}/values", {
          params: { path: { field_id: fieldId } },
          body: { value, description: "" },
        }),
      ),
    onSuccess: refresh,
  });
  return { approve, deprecate, merge, rename, synonym, createField, createValue };
}

export function useReview(query: object) {
  return useInfiniteQuery({
    queryKey: aiKeys.review(query),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }): Promise<ReviewPage> =>
      unwrap(
        api.GET("/api/v1/review", {
          params: { query: { ...query, limit: 30, cursor: pageParam } },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

function refreshAsset(client: ReturnType<typeof useQueryClient>, id?: string) {
  if (id) void client.invalidateQueries({ queryKey: ["asset", id] });
  void client.invalidateQueries({ queryKey: ["review"] });
  void client.invalidateQueries({ queryKey: ["assets"] });
  void client.invalidateQueries({ queryKey: ["facets"] });
}

/** Human edits of an asset (`PATCH /assets/{id}`), also used to approve a review item. */
export function useEditAsset() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & Patch<AssetEdit>) =>
      unwrap(
        api.PATCH("/api/v1/assets/{asset_id}", {
          params: { path: { asset_id: id } },
          body: body as AssetEdit,
        }),
      ),
    onSuccess: (_, vars) => refreshAsset(client, vars.id),
  });
}

export function useEditShot() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, assetId, ...body }: { id: string; assetId: string } & Patch<ShotEdit>) =>
      unwrap(
        api.PATCH("/api/v1/shots/{shot_id}", {
          params: { path: { shot_id: id } },
          body: body as ShotEdit,
        }),
      ).then((result) => ({ result, assetId })),
    onSuccess: ({ assetId }) => refreshAsset(client, assetId),
  });
}

export function useBulkEdit() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: Patch<BulkEdit> & { asset_ids: string[] }) =>
      unwrap(api.POST("/api/v1/assets/bulk", { body: body as BulkEdit })),
    onSuccess: () => {
      refreshAsset(client);
      void client.invalidateQueries({ queryKey: aiKeys.collections });
    },
  });
}

export function useCollections() {
  return useQuery({
    queryKey: aiKeys.collections,
    queryFn: (): Promise<Collection[]> => unwrap(api.GET("/api/v1/collections")),
  });
}

export function useCreateCollection() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => unwrap(api.POST("/api/v1/collections", { body: { name } })),
    onSuccess: () => void client.invalidateQueries({ queryKey: aiKeys.collections }),
  });
}

export function useGoldenFields() {
  return useQuery({
    queryKey: aiKeys.goldenFields,
    queryFn: (): Promise<GoldenField[]> => unwrap(api.GET("/api/v1/golden/fields")),
    staleTime: 60_000,
  });
}

export function useGoldenItems(query: { unlabeled: boolean; offset: number }) {
  return useQuery({
    queryKey: aiKeys.golden(query),
    queryFn: (): Promise<GoldenPage> =>
      unwrap(api.GET("/api/v1/golden/items", { params: { query: { ...query, limit: 5 } } })),
  });
}

export function useGoldenStats() {
  return useQuery({
    queryKey: aiKeys.goldenStats,
    queryFn: (): Promise<GoldenStats> => unwrap(api.GET("/api/v1/golden/stats")),
  });
}

export function useSetLabel() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      asset_id: string;
      shot_id: string | null;
      field: string;
      expected: string;
    }) => unwrap(api.PUT("/api/v1/golden/labels", { body })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: aiKeys.goldenStats });
      void client.invalidateQueries({ queryKey: ["golden"] });
    },
  });
}

export function useEvalRuns(enabled: boolean) {
  return useQuery({
    queryKey: aiKeys.evalRuns,
    enabled,
    queryFn: (): Promise<EvalRun[]> => unwrap(api.GET("/api/v1/eval/runs")),
  });
}

export function useRunEval() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/admin/eval", { body: {} })),
    onSuccess: () => void client.invalidateQueries({ queryKey: aiKeys.evalRuns }),
  });
}

export function useReembed() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/admin/reembed")),
    onSuccess: () => void client.invalidateQueries({ queryKey: aiKeys.capabilities }),
  });
}
