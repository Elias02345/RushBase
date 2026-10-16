/** Queries and mutations of needs lists and the MCP / Claude administration. */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "./client";
import type { McpAuditPage, McpStatus, NeedDetail, NeedInput, NeedPage } from "./contracts";

export const needKeys = {
  all: ["needs"] as const,
  list: (status: string) => ["needs", "list", status] as const,
  detail: (id: string) => ["needs", "detail", id] as const,
  mcp: ["mcp-status"] as const,
  audit: (query: object) => ["mcp-audit", query] as const,
};

export function useNeedLists(status: "open" | "closed") {
  return useQuery({
    queryKey: needKeys.list(status),
    queryFn: (): Promise<NeedPage> =>
      unwrap(api.GET("/api/v1/needs", { params: { query: { status, limit: 100 } } })),
  });
}

export function useNeed(id: string | undefined) {
  return useQuery({
    queryKey: needKeys.detail(id ?? ""),
    enabled: id !== undefined,
    queryFn: (): Promise<NeedDetail> =>
      unwrap(api.GET("/api/v1/needs/{need_id}", { params: { path: { need_id: id ?? "" } } })),
  });
}

/** Needs mutations; every one refreshes the lists and the open detail. */
export function useNeedActions() {
  const client = useQueryClient();
  const refresh = () => void client.invalidateQueries({ queryKey: needKeys.all });
  const create = useMutation({
    mutationFn: (body: NeedInput): Promise<NeedDetail> =>
      unwrap(api.POST("/api/v1/needs", { body })),
    onSuccess: refresh,
  });
  const setStatus = useMutation({
    mutationFn: (v: { id: string; status: "open" | "closed" }): Promise<NeedDetail> =>
      unwrap(
        api.PATCH("/api/v1/needs/{need_id}", {
          params: { path: { need_id: v.id } },
          body: { status: v.status },
        }),
      ),
    onSuccess: refresh,
  });
  const decide = useMutation({
    mutationFn: (v: {
      item: string;
      asset: string;
      status: "confirmed" | "rejected" | "candidate";
    }) =>
      unwrap(
        api.PUT("/api/v1/needs/items/{item_id}/candidates/{asset_id}", {
          params: { path: { item_id: v.item, asset_id: v.asset } },
          body: { status: v.status },
        }),
      ),
    onSuccess: refresh,
  });
  const setItem = useMutation({
    mutationFn: (v: { id: string; status: "open" | "fulfilled" | "dropped" }) =>
      unwrap(
        api.PATCH("/api/v1/needs/items/{item_id}", {
          params: { path: { item_id: v.id } },
          body: { status: v.status },
        }),
      ),
    onSuccess: refresh,
  });
  return { create, setStatus, decide, setItem };
}

export function useMcpStatus() {
  return useQuery({
    queryKey: needKeys.mcp,
    queryFn: (): Promise<McpStatus> => unwrap(api.GET("/api/v1/admin/mcp")),
  });
}

export function useMcpActions() {
  const client = useQueryClient();
  const refresh = () => void client.invalidateQueries({ queryKey: needKeys.mcp });
  const revoke = useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST("/api/v1/admin/mcp/clients/{client_pk}/revoke", {
          params: { path: { client_pk: id } },
        }),
      ),
    onSuccess: refresh,
  });
  const redirects = useMutation({
    mutationFn: (uris: string[]) =>
      unwrap(api.PUT("/api/v1/admin/mcp/redirect-uris", { body: { uris } })),
    onSuccess: refresh,
  });
  return { revoke, redirects };
}

export function useMcpAudit(query: { client_id?: string; tool?: string }) {
  return useQuery({
    queryKey: needKeys.audit(query),
    queryFn: (): Promise<McpAuditPage> =>
      unwrap(api.GET("/api/v1/admin/mcp/audit", { params: { query: { ...query, limit: 50 } } })),
    placeholderData: keepPreviousData,
  });
}
