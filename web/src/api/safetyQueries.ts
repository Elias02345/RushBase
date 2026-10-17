/** Trash, privacy lock, sync suggestions and the content migration tool. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "./client";
import type { components } from "@rushbase/api-client";

type Schemas = components["schemas"];
export type TrashList = Schemas["TrashList"];
export type TrashedItem = Schemas["TrashedOut"];
export type LockResult = Schemas["LockOut"];
export type LockRequest = Schemas["LockIn"];
export type MigrationPreview = Schemas["PreviewOut"];
export type MigrationItem = Schemas["MigrationItemOut"];
export type MigrationResult = Schemas["ExecuteOut"];
export type SyncSuggestion = Schemas["SyncOut"];

export const safetyKeys = {
  trash: ["trash"] as const,
  migration: ["migration-settings"] as const,
};

function refreshAssets(client: ReturnType<typeof useQueryClient>) {
  void client.invalidateQueries({ queryKey: ["assets"] });
  void client.invalidateQueries({ queryKey: ["asset"] });
  void client.invalidateQueries({ queryKey: ["facets"] });
  void client.invalidateQueries({ queryKey: ["stats"] });
  void client.invalidateQueries({ queryKey: safetyKeys.trash });
}

export function useTrash() {
  return useQuery({
    queryKey: safetyKeys.trash,
    queryFn: (): Promise<TrashList> => unwrap(api.GET("/api/v1/admin/trash")),
  });
}

/** Move an asset to the trash (the dialog has been confirmed). */
export function useTrashAsset() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST("/api/v1/assets/{asset_id}/trash", {
          params: { path: { asset_id: id } },
          body: { confirm: true },
        }),
      ),
    onSuccess: () => refreshAssets(client),
  });
}

export function useRestoreTrashed() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST("/api/v1/admin/trash/{asset_id}/restore", {
          params: { path: { asset_id: id } },
        }),
      ),
    onSuccess: () => refreshAssets(client),
  });
}

/** Dry run or real lock of the assets of a batch or person. */
export function usePrivacyLock() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: LockRequest): Promise<LockResult> =>
      unwrap(api.POST("/api/v1/privacy/lock", { body })),
    onSuccess: (_, vars) => {
      if (vars.dry_run === false) refreshAssets(client);
    },
  });
}

export function useDecideSync() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "confirmed" | "rejected" }) =>
      unwrap(
        api.POST("/api/v1/sync-suggestions/{suggestion_id}", {
          params: { path: { suggestion_id: id } },
          body: { decision },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["asset"] }),
  });
}

export function useMigrationSettings() {
  return useQuery({
    queryKey: safetyKeys.migration,
    queryFn: () => unwrap(api.GET("/api/v1/admin/migration/settings")),
  });
}

export function useSetMigrationEnabled() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) =>
      unwrap(api.PUT("/api/v1/admin/migration/settings", { body: { enabled } })),
    onSuccess: (data) => client.setQueryData(safetyKeys.migration, data),
  });
}

export interface MigrationRequest {
  batch_id?: string;
  asset_ids?: string[];
  template: string;
}

export function useMigrationPreview() {
  return useMutation({
    mutationFn: (body: MigrationRequest): Promise<MigrationPreview> =>
      unwrap(api.POST("/api/v1/admin/migration/preview", { body })),
  });
}

export function useMigrationExecute() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: MigrationRequest & { confirmation: string }): Promise<MigrationResult> =>
      unwrap(api.POST("/api/v1/admin/migration/execute", { body })),
    onSuccess: () => refreshAssets(client),
  });
}
