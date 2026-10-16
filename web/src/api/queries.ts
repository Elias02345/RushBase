import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api, ApiError, unwrap } from "./client";
import type {
  AssetDetail,
  AssetPage,
  AssetQuery,
  FacetsResponse,
  FilingDryRun,
  FilingRule,
  LibrarySettings,
  QuarantineItem,
  StatsResponse,
} from "./contracts";
import type { Health, Role, SetupStatus, SystemStatus } from "./types";

export const keys = {
  me: ["me"] as const,
  health: ["health"] as const,
  setup: ["setup"] as const,
  status: ["status"] as const,
  assets: (query: AssetQuery) => ["assets", query] as const,
  asset: (id: string) => ["asset", id] as const,
  facets: (query: FacetQuery) => ["facets", query] as const,
  stats: ["stats"] as const,
  batches: ["batches"] as const,
  workers: ["workers"] as const,
  users: ["users"] as const,
  tokens: ["enrollment-tokens"] as const,
  backups: ["backups"] as const,
  librarySettings: ["library-settings"] as const,
  filingRules: ["filing-rules"] as const,
  quarantine: ["quarantine"] as const,
};

/** The filters and query that narrow the facet counts (same parameters as the asset list). */
export type FacetQuery = Omit<AssetQuery, "cursor" | "limit" | "sort">;

/** `/api/health` answers 503 with a valid body while degraded, so that still resolves. */
export async function fetchHealth(): Promise<Health> {
  const response = await fetch("/api/health", { headers: { accept: "application/json" } });
  return (await response.json()) as Health;
}

export function useHealth() {
  return useQuery({ queryKey: keys.health, queryFn: fetchHealth, retry: false });
}

export async function fetchMe() {
  return unwrap(api.GET("/api/v1/auth/me"));
}

export function useMe() {
  return useQuery({ queryKey: keys.me, queryFn: fetchMe, retry: false, staleTime: 60_000 });
}

export function fetchSetup(): Promise<SetupStatus> {
  return unwrap(api.GET("/api/v1/setup"));
}

export function useSetupStatus() {
  return useQuery({ queryKey: keys.setup, queryFn: fetchSetup, retry: false });
}

/** Live backlog counters; polls so the pipeline visual follows the queue. */
export function useStatus() {
  return useQuery({
    queryKey: keys.status,
    queryFn: (): Promise<SystemStatus> => unwrap(api.GET("/api/v1/status")),
    refetchInterval: 5_000,
  });
}

/** Cursor-paged search; the next page is requested by the grid while scrolling. */
export function useAssets(query: Omit<AssetQuery, "cursor">) {
  return useInfiniteQuery({
    queryKey: keys.assets(query),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }): Promise<AssetPage> =>
      unwrap(
        api.GET("/api/v1/assets", {
          params: { query: { ...query, limit: 60, cursor: pageParam } },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
  });
}

export function useAsset(id: string) {
  return useQuery({
    queryKey: keys.asset(id),
    queryFn: (): Promise<AssetDetail> =>
      unwrap(api.GET("/api/v1/assets/{asset_id}", { params: { path: { asset_id: id } } })),
  });
}

export function useFacets(query: FacetQuery) {
  return useQuery({
    queryKey: keys.facets(query),
    queryFn: (): Promise<FacetsResponse> =>
      unwrap(api.GET("/api/v1/facets", { params: { query } })),
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  });
}

export function useStats() {
  return useQuery({
    queryKey: keys.stats,
    queryFn: (): Promise<StatsResponse> => unwrap(api.GET("/api/v1/stats")),
    staleTime: 30_000,
  });
}

export function useBatches() {
  return useQuery({
    queryKey: keys.batches,
    queryFn: () => unwrap(api.GET("/api/v1/admin/batches")),
    refetchInterval: 8_000,
  });
}

export function useWorkers(enabled: boolean) {
  return useQuery({
    queryKey: keys.workers,
    queryFn: () => unwrap(api.GET("/api/v1/admin/workers")),
    refetchInterval: 8_000,
    enabled,
  });
}

export function useUsers() {
  return useQuery({
    queryKey: keys.users,
    queryFn: () => unwrap(api.GET("/api/v1/admin/users")),
  });
}

export function useEnrollmentTokens() {
  return useQuery({
    queryKey: keys.tokens,
    queryFn: () => unwrap(api.GET("/api/v1/admin/enrollment-tokens")),
  });
}

export function useBackups() {
  return useQuery({
    queryKey: keys.backups,
    queryFn: () => unwrap(api.GET("/api/v1/admin/backups")),
  });
}

export function useLibrarySettings() {
  return useQuery({
    queryKey: keys.librarySettings,
    queryFn: (): Promise<LibrarySettings> => unwrap(api.GET("/api/v1/admin/library-settings")),
  });
}

/** Rule versions, newest first. */
export function useFilingRules() {
  return useQuery({
    queryKey: keys.filingRules,
    queryFn: (): Promise<FilingRule[]> => unwrap(api.GET("/api/v1/filing-rules")),
  });
}

export function useQuarantine(enabled: boolean) {
  return useQuery({
    queryKey: keys.quarantine,
    queryFn: (): Promise<QuarantineItem[]> => unwrap(api.GET("/api/v1/quarantine")),
    enabled,
  });
}

/** Whether `role` may do what `needed` requires (viewer < editor < admin). */
export function hasRole(role: Role | undefined, needed: Role): boolean {
  const rank: Record<Role, number> = { viewer: 0, editor: 1, admin: 2 };
  return role !== undefined && rank[role] >= rank[needed];
}

// --- mutations ----------------------------------------------------------------------------

export function useLogin() {
  const client = useQueryClient();
  return useMutation({
    mutationKey: ["login"],
    mutationFn: (body: { username: string; password: string }) =>
      unwrap(api.POST("/api/v1/auth/login", { body })),
    onSuccess: (result) => client.setQueryData(keys.me, result.user),
  });
}

export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/auth/logout")),
    onSuccess: () => client.clear(),
  });
}

export function useSetup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      username: string;
      password: string;
      display_name: string;
      library_name: string;
      locale: "de" | "en";
    }) => unwrap(api.POST("/api/v1/setup", { body })),
    onSuccess: (result) => client.setQueryData(keys.me, result.user),
  });
}

export function useRestore() {
  return useMutation({
    mutationFn: (backup_name: string) =>
      unwrap(api.POST("/api/v1/setup/restore", { body: { backup_name } })),
  });
}

export function useCreateEnrollment() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (note: string) =>
      unwrap(api.POST("/api/v1/admin/enrollment-tokens", { body: { note } })),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.tokens }),
  });
}

export function useRevokeEnrollment() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST("/api/v1/admin/enrollment-tokens/{token_id}/revoke", {
          params: { path: { token_id: id } },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.tokens }),
  });
}

export function useRevokeWorker() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST("/api/v1/admin/workers/{worker_id}/revoke", {
          params: { path: { worker_id: id } },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.workers }),
  });
}

export function useQueuePaused() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (paused: boolean) =>
      unwrap(api.POST(paused ? "/api/v1/admin/queue/pause" : "/api/v1/admin/queue/resume")),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.status }),
  });
}

export function useBatchControl() {
  const client = useQueryClient();
  const refresh = () => void client.invalidateQueries({ queryKey: keys.batches });
  const pause = useMutation({
    mutationFn: ({ id, paused }: { id: string; paused: boolean }) =>
      unwrap(
        api.POST(
          paused
            ? "/api/v1/admin/batches/{batch_id}/pause"
            : "/api/v1/admin/batches/{batch_id}/resume",
          { params: { path: { batch_id: id } } },
        ),
      ),
    onSuccess: refresh,
  });
  const priority = useMutation({
    mutationFn: ({ id, value }: { id: string; value: number }) =>
      unwrap(
        api.PUT("/api/v1/admin/batches/{batch_id}/priority", {
          params: { path: { batch_id: id } },
          body: { priority: value },
        }),
      ),
    onSuccess: refresh,
  });
  return { pause, priority };
}

/** Move a quarantined asset back to `Input/` and requeue its jobs. */
export function useRetryQuarantined() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST("/api/v1/quarantine/{asset_id}/retry", { params: { path: { asset_id: id } } }),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.quarantine });
      void client.invalidateQueries({ queryKey: ["assets"] });
    },
  });
}

export function useCreateUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      username: string;
      password: string;
      display_name: string;
      role: Role;
      locale: "de" | "en";
    }) => unwrap(api.POST("/api/v1/admin/users", { body })),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.users }),
  });
}

export function useUpdateUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      ...body
    }: {
      id: string;
      role?: Role;
      disabled?: boolean;
      password?: string;
    }) =>
      unwrap(
        api.PATCH("/api/v1/admin/users/{user_id}", { params: { path: { user_id: id } }, body }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.users }),
  });
}

export function useCreateBackup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/admin/backups")),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.backups }),
  });
}

export function useSaveLibrarySettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: LibrarySettings) =>
      unwrap(api.PUT("/api/v1/admin/library-settings", { body })),
    onSuccess: (data) => client.setQueryData(keys.librarySettings, data),
  });
}

export function useCreateFilingRule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { template: string; fallbacks?: Record<string, string>; note?: string }) =>
      unwrap(api.POST("/api/v1/filing-rules", { body: { note: "", ...body } })),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.filingRules }),
  });
}

export function useActivateFilingRule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST("/api/v1/filing-rules/{rule_id}/activate", { params: { path: { rule_id: id } } }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.filingRules }),
  });
}

export function useDryRunFilingRule() {
  return useMutation({
    mutationFn: (body: {
      template: string;
      fallbacks?: Record<string, string>;
    }): Promise<FilingDryRun> =>
      unwrap(api.POST("/api/v1/filing-rules/dry-run", { body: { limit: 200, ...body } })),
  });
}

export { ApiError };
