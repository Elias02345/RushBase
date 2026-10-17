/** Queries and mutations of usage feedback, personal tokens, reprocessing, dashboard and audit. */
import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api, unwrap } from "./client";
import type {
  AssetNeedLink,
  AuditLogPage,
  ByModelResult,
  GrowthMonth,
  Heatmap,
  HeatDimension,
  ModelUse,
  PersonalToken,
  PersonalTokenCreated,
  ReprocessField,
  ReprocessOp,
  Throughput,
} from "./contracts";
import { keys } from "./queries";

export interface AuditFilters {
  actor_kind?: string;
  actor_id?: string;
  action?: string;
  target_kind?: string;
  target_id?: string;
  since?: string;
  until?: string;
}

export const finalKeys = {
  tokens: ["personal-tokens"] as const,
  models: ["reprocess-models"] as const,
  growth: ["stats-growth"] as const,
  throughput: ["stats-throughput"] as const,
  dimensions: ["stats-dimensions"] as const,
  heatmap: (rows: string, columns: string) => ["stats-heatmap", rows, columns] as const,
  audit: (filters: AuditFilters) => ["audit-log", filters] as const,
  auditActions: ["audit-actions"] as const,
  assetNeeds: (id: string) => ["needs", "asset", id] as const,
};

// --- used in project ---------------------------------------------------------------------

/** Mark an asset as used in a project by hand, or remove a mark. */
export function useUsageActions(assetId: string) {
  const client = useQueryClient();
  const refresh = () => {
    void client.invalidateQueries({ queryKey: keys.asset(assetId) });
    void client.invalidateQueries({ queryKey: ["facets"] });
  };
  const mark = useMutation({
    mutationFn: (project_name: string) =>
      unwrap(
        api.PUT("/api/v1/assets/{asset_id}/usage", {
          params: { path: { asset_id: assetId } },
          body: { project_name },
        }),
      ),
    onSuccess: refresh,
  });
  const unmark = useMutation({
    mutationFn: (v: { project_name: string; source: "manual" | "resolve" | "claude" }) =>
      unwrap(
        api.DELETE("/api/v1/assets/{asset_id}/usage", {
          params: { path: { asset_id: assetId }, query: v },
        }),
      ),
    onSuccess: refresh,
  });
  return { mark, unmark };
}

// --- personal API tokens -----------------------------------------------------------------

export function usePersonalTokens() {
  return useQuery({
    queryKey: finalKeys.tokens,
    queryFn: (): Promise<PersonalToken[]> => unwrap(api.GET("/api/v1/tokens")),
  });
}

export function useCreatePersonalToken() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (v: {
      name: string;
      readOnly: boolean;
      expiresInDays: number | null;
    }): Promise<PersonalTokenCreated> =>
      unwrap(
        api.POST("/api/v1/tokens", {
          body: {
            name: v.name,
            scopes: v.readOnly ? ["read"] : [],
            expires_in_days: v.expiresInDays,
          },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: finalKeys.tokens }),
  });
}

export function useRevokePersonalToken() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/tokens/{token_id}", { params: { path: { token_id: id } } })),
    onSuccess: () => void client.invalidateQueries({ queryKey: finalKeys.tokens }),
  });
}

// --- reprocess by model ------------------------------------------------------------------

export function useReprocessModels(enabled: boolean) {
  return useQuery({
    queryKey: finalKeys.models,
    enabled,
    queryFn: (): Promise<ModelUse[]> => unwrap(api.GET("/api/v1/admin/reprocess/models")),
  });
}

export interface ByModelRequest {
  stage: ModelUse["stage"];
  field: ReprocessField;
  op: ReprocessOp;
  value: string;
  dry_run: boolean;
}

export function useReprocessByModel() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: ByModelRequest): Promise<ByModelResult> =>
      unwrap(api.POST("/api/v1/admin/reprocess/by-model", { body })),
    onSuccess: (result) => {
      if (!result.dry_run) {
        void client.invalidateQueries({ queryKey: keys.status });
        void client.invalidateQueries({ queryKey: finalKeys.models });
      }
    },
  });
}

// --- dashboard ---------------------------------------------------------------------------

export function useGrowth() {
  return useQuery({
    queryKey: finalKeys.growth,
    queryFn: (): Promise<GrowthMonth[]> => unwrap(api.GET("/api/v1/stats/growth")),
    staleTime: 60_000,
  });
}

export function useThroughput() {
  return useQuery({
    queryKey: finalKeys.throughput,
    queryFn: (): Promise<Throughput> => unwrap(api.GET("/api/v1/stats/throughput")),
    staleTime: 60_000,
  });
}

export function useHeatDimensions() {
  return useQuery({
    queryKey: finalKeys.dimensions,
    queryFn: (): Promise<HeatDimension[]> => unwrap(api.GET("/api/v1/stats/dimensions")),
    staleTime: 5 * 60_000,
  });
}

export function useHeatmap(rows: string, columns: string) {
  return useQuery({
    queryKey: finalKeys.heatmap(rows, columns),
    enabled: rows !== "" && columns !== "",
    queryFn: (): Promise<Heatmap> =>
      unwrap(api.GET("/api/v1/stats/coverage", { params: { query: { rows, columns } } })),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });
}

// --- audit log ---------------------------------------------------------------------------

export function useAuditLog(filters: AuditFilters) {
  return useInfiniteQuery({
    queryKey: finalKeys.audit(filters),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }): Promise<AuditLogPage> =>
      unwrap(
        api.GET("/api/v1/admin/audit", {
          params: { query: { ...filters, cursor: pageParam, limit: 50 } },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
  });
}

export function useAuditActions() {
  return useQuery({
    queryKey: finalKeys.auditActions,
    queryFn: (): Promise<string[]> => unwrap(api.GET("/api/v1/admin/audit/actions")),
    staleTime: 60_000,
  });
}

// --- needs of an asset -------------------------------------------------------------------

export function useAssetNeeds(assetId: string) {
  return useQuery({
    queryKey: finalKeys.assetNeeds(assetId),
    queryFn: (): Promise<AssetNeedLink[]> =>
      unwrap(
        api.GET("/api/v1/needs/for-asset/{asset_id}", { params: { path: { asset_id: assetId } } }),
      ),
  });
}
