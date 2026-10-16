import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { cliData } from "../bridge";
import { useBridge } from "../bridge/context";
import type {
  AssetDetail,
  AssetSummary,
  Basket,
  BrowsePage,
  Facet,
  ResolveStatus,
  SearchPage,
} from "../bridge/types";

const PAGE_SIZE = 24;

export interface LibraryQuery {
  text: string;
  filters: Record<string, string>;
  sort: string;
}

export interface LibraryPage {
  items: AssetSummary[];
  next: string | null;
  total: number | null;
}

const filterArgs = (filters: Record<string, string>) =>
  Object.entries(filters)
    .filter(([, value]) => value)
    .flatMap(([key, value]) => ["--filter", `${key}=${value}`]);

/** Library grid: browse without a query, hybrid search with one; both paginate. */
export function useLibrary(query: LibraryQuery, enabled: boolean) {
  const bridge = useBridge();
  return useInfiniteQuery<LibraryPage, Error, { pages: LibraryPage[] }, unknown[], string>({
    queryKey: ["library", query],
    enabled,
    initialPageParam: "",
    retry: false,
    getNextPageParam: (last) => last.next,
    queryFn: async ({ pageParam }) => {
      const text = query.text.trim();
      if (text) {
        const offset = pageParam || "0";
        const page = await cliData<SearchPage>(bridge, [
          "search",
          text,
          "--limit",
          String(PAGE_SIZE),
          "--offset",
          offset,
          ...filterArgs(query.filters),
        ]);
        const seen = new Set<string>();
        const items = page.items
          .map((hit) => hit.asset)
          .filter((a) => !seen.has(a.id) && seen.add(a.id));
        return {
          items,
          next: page.next_offset != null ? String(page.next_offset) : null,
          total: page.candidates,
        };
      }
      const args = ["browse", "--limit", String(PAGE_SIZE), ...filterArgs(query.filters)];
      if (query.sort) args.push("--sort", query.sort);
      if (pageParam) args.push("--cursor", pageParam);
      const page = await cliData<BrowsePage>(bridge, args);
      return { items: page.items, next: page.next_cursor, total: page.total };
    },
  });
}

/** Filter dimensions (enum facets only) for the toolbar. */
export function useFacets(enabled: boolean) {
  const bridge = useBridge();
  return useQuery({
    queryKey: ["facets"],
    enabled,
    retry: false,
    queryFn: async () =>
      (await cliData<{ facets: Facet[] }>(bridge, ["filters"])).facets.filter(
        (f) => f.kind === "enum",
      ),
  });
}

export function useAsset(id: string | null) {
  const bridge = useBridge();
  return useQuery({
    queryKey: ["asset", id],
    enabled: id !== null,
    retry: false,
    queryFn: () => cliData<AssetDetail>(bridge, ["asset", "get", id ?? ""]),
  });
}

/** The selection basket and its mutations (`rushbase select ...`). */
export function useBasket() {
  const bridge = useBridge();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["basket"],
    queryFn: () => cliData<Basket>(bridge, ["select", "list"]),
  });
  const mutate = useCallback(
    async (args: string[]) => {
      const next = await cliData<Basket>(bridge, ["select", ...args]);
      client.setQueryData(["basket"], next);
    },
    [bridge, client],
  );
  return {
    basket: query.data ?? { count: 0, items: [] },
    add: (id: string, shots?: number[]) =>
      mutate(["add", id, ...(shots ? ["--shots", shots.join(",")] : [])]),
    remove: (id: string) => mutate(["remove", id]),
    clear: () => mutate(["clear"]),
  };
}

/** Whether Resolve Studio is installed and reachable (hides the push button otherwise). */
export function useResolve() {
  const bridge = useBridge();
  return useQuery({
    queryKey: ["resolve"],
    staleTime: 60_000,
    retry: false,
    queryFn: async (): Promise<ResolveStatus> => {
      const result = await bridge.cli<ResolveStatus>(["resolve", "status", "--json"]);
      const data = result.envelope?.data;
      if (!data || ("error" in data && !("installed" in data))) throw new Error(result.stderr);
      return data as ResolveStatus;
    },
  });
}
