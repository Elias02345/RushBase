import {
  Button,
  DataTable,
  EmptyState,
  IconButton,
  SearchField,
  Select,
  Skeleton,
  StatusChip,
  type DataColumn,
} from "@rushbase/ui";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { FilterX, FolderSearch, LayoutGrid, List, ListPlus, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { BulkPrivacy } from "./library/BulkPrivacy";
import { useBulkEdit, useCollections, useHybridSearch } from "../api/aiQueries";
import type { AssetSummary, SortKey } from "../api/contracts";
import { useAssets, useFacets } from "../api/queries";
import {
  formatBytes,
  formatDate,
  formatDuration,
  formatNumber,
  formatResolution,
} from "../lib/format";
import { assetTone } from "../lib/status";
import { facetQuery, type LibrarySearch } from "../routes/search";
import { AssetGrid, type SelectModifiers } from "./library/AssetGrid";
import { valueLabel } from "../lib/facets";
import { FacetSidebar } from "./library/Facets";
import { HitCard } from "./library/HitCard";

const SORTS: SortKey[] = ["-date", "date", "filename", "-size", "-duration", "-created_at"];
/** Search results rank by relevance until the user picks an explicit order. */
const RELEVANCE = "relevance";
const FILTER_KEYS = [
  "media_type",
  "status",
  "camera_model",
  "lens_model",
  "log_profile",
  "orientation",
  "date_from",
  "date_to",
  "batch_id",
  "shot_size",
  "camera_angle",
  "interior_exterior",
  "time_of_day",
  "person_identifiable",
  "review_state",
  "consent",
  "collection_id",
] as const;

export function LibraryPage() {
  const { t, i18n } = useTranslation();
  const search = useSearch({ from: "/_app/library" });
  const navigate = useNavigate();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [text, setText] = useState(search.q ?? "");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const anchor = useRef(0);
  const pushedQuery = useRef<string | undefined>(search.q);
  const [listHeight, setListHeight] = useState(520);

  const view = search.view ?? "grid";
  const level = search.level ?? "asset";
  // A text query without an explicit order runs the hybrid search (relevance, scenes).
  const hybrid = Boolean(search.q) && !search.sort;
  const query = useMemo(() => ({ ...search, view: undefined, level: undefined }), [search]);
  const assets = useAssets(query);
  const hybridQuery = useMemo(
    () => ({ ...facetQuery(search), q: search.q ?? "", level }),
    [search, level],
  );
  const found = useHybridSearch(hybridQuery, hybrid);
  const hits = useMemo(() => found.data?.pages.flatMap((page) => page.items) ?? [], [found.data]);
  const skipped = found.data?.pages[0]?.skipped ?? {};
  const collections = useCollections();
  const bulk = useBulkEdit();
  const facets = useFacets(useMemo(() => facetQuery(search), [search]));
  const items = useMemo(
    () => assets.data?.pages.flatMap((page) => page.items) ?? [],
    [assets.data],
  );
  const total = hybrid ? null : (assets.data?.pages[0]?.total ?? null);

  const update = useCallback(
    (patch: Partial<LibrarySearch>) => {
      void navigate({
        to: "/library",
        search: (previous: LibrarySearch) => ({ ...previous, ...patch }),
        replace: true,
      });
      setSelected(new Set());
    },
    [navigate],
  );

  // URL -> field (top-bar search, back button); field -> URL is debounced below.
  // Only external changes (not our own debounced push) overwrite what the user is typing.
  useEffect(() => {
    if (search.q === pushedQuery.current) return;
    pushedQuery.current = search.q;
    setText(search.q ?? "");
  }, [search.q]);
  useEffect(() => {
    if (text === (search.q ?? "")) return;
    const handle = window.setTimeout(() => {
      pushedQuery.current = text.trim() || undefined;
      update({ q: pushedQuery.current });
    }, 250);
    return () => window.clearTimeout(handle);
  }, [text, search.q, update]);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setListHeight(Math.max(240, entry.contentRect.height - 64));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const active = hybrid ? found : assets;
  const fetchNext = active.fetchNextPage;
  const hasNext = active.hasNextPage;
  const fetching = active.isFetchingNextPage;
  const onNearEnd = useCallback(() => {
    if (hasNext && !fetching) void fetchNext();
  }, [hasNext, fetching, fetchNext]);

  const toggle = (index: number, modifiers: SelectModifiers) => {
    setSelected((current) => {
      const next = new Set(current);
      if (modifiers.shift) {
        const [from, to] = [Math.min(anchor.current, index), Math.max(anchor.current, index)];
        for (const asset of items.slice(from, to + 1)) next.add(asset.id);
      } else {
        const id = items[index]?.id;
        if (id) {
          if (next.has(id)) next.delete(id);
          else next.add(id);
        }
        anchor.current = index;
      }
      return next;
    });
  };

  const openAsset = (asset: AssetSummary) =>
    void navigate({ to: "/assets/$id", params: { id: asset.id } });

  const activeFilters = FILTER_KEYS.filter((key) => search[key]);
  const tagFilters = search.tag ?? [];
  const columns: DataColumn<AssetSummary>[] = [
    {
      key: "name",
      header: t("library.col.name"),
      width: "minmax(0, 2.2fr)",
      cell: (a) => <strong>{a.title ?? a.filename}</strong>,
    },
    {
      key: "type",
      header: t("library.col.type"),
      width: "90px",
      cell: (a) => t(`media.${a.media_type}`),
    },
    {
      key: "duration",
      header: t("library.col.duration"),
      width: "90px",
      cell: (a) => formatDuration(a.duration_ms),
    },
    {
      key: "res",
      header: t("library.col.resolution"),
      width: "100px",
      cell: (a) => formatResolution(a.width, a.height) ?? "",
    },
    { key: "camera", header: t("library.col.camera"), cell: (a) => a.camera_model ?? "" },
    {
      key: "date",
      header: t("library.col.date"),
      width: "120px",
      cell: (a) => formatDate(a.capture_at),
    },
    {
      key: "size",
      header: t("library.col.size"),
      width: "90px",
      align: "end",
      cell: (a) => formatBytes(a.size_total),
    },
    {
      key: "status",
      header: t("library.col.status"),
      width: "130px",
      cell: (a) => (
        <StatusChip tone={assetTone(a.status)}>{t(`assetStatus.${a.status}`)}</StatusChip>
      ),
    },
  ];

  return (
    <div className="library">
      <aside className="library__facets" aria-label={t("library.filters")}>
        <div className="library__facets-head">
          <h2>{t("library.filters")}</h2>
          {activeFilters.length > 0 ? (
            <Button
              variant="ghost"
              size="sm"
              icon={<FilterX size={14} />}
              onClick={() =>
                update(
                  Object.fromEntries(
                    FILTER_KEYS.map((k) => [k, undefined]),
                  ) as Partial<LibrarySearch>,
                )
              }
            >
              {t("library.clearFilters")}
            </Button>
          ) : null}
        </div>
        {facets.data ? (
          <FacetSidebar
            facets={facets.data.facets}
            search={search}
            language={i18n.language}
            onChange={update}
          />
        ) : (
          <div className="facets">
            <Skeleton height={20} width="60%" />
            <Skeleton height={120} />
            <Skeleton height={20} width="50%" />
            <Skeleton height={120} />
          </div>
        )}
      </aside>

      <section className="library__main" aria-label={t("library.results")}>
        <div className="library__toolbar">
          <SearchField
            data-search-input=""
            variant="lg"
            value={text}
            onValueChange={setText}
            clearLabel={t("common.clear")}
            aria-label={t("library.searchLabel")}
            placeholder={t("library.searchPlaceholder")}
            shortcut="/"
            className="library__search"
          />
          <Select
            aria-label={t("library.sort")}
            value={search.sort ?? (search.q ? RELEVANCE : "-date")}
            onValueChange={(value) =>
              update({ sort: value === RELEVANCE ? undefined : (value as SortKey) })
            }
            options={[...(search.q ? [RELEVANCE] : []), ...SORTS].map((key) => ({
              value: key,
              label: t(`library.sorts.${key}`),
            }))}
            className="library__sort"
          />
          {search.q ? (
            <div className="segmented segmented--text" role="group" aria-label={t("search.level")}>
              {(["asset", "shot"] as const).map((key) => (
                <button
                  key={key}
                  type="button"
                  className="segmented__btn"
                  aria-pressed={level === key}
                  onClick={() => update({ level: key === "asset" ? undefined : key })}
                >
                  {t(`search.levels.${key}`)}
                </button>
              ))}
            </div>
          ) : null}
          <div className="segmented" role="group" aria-label={t("library.view")}>
            <IconButton
              label={t("library.viewGrid")}
              icon={<LayoutGrid size={18} />}
              pressed={view === "grid"}
              onClick={() => update({ view: undefined })}
            />
            <IconButton
              label={t("library.viewList")}
              icon={<List size={18} />}
              pressed={view === "list"}
              onClick={() => update({ view: "list" })}
            />
          </div>
        </div>

        <div className="library__summary" aria-live="polite">
          <span>
            {total !== null
              ? t("library.count", { count: total, formatted: formatNumber(total) })
              : hybrid && found.data
                ? t("search.count", { count: found.data.pages[0]?.candidates ?? 0 })
                : ""}
          </span>
          <ul className="chips">
            {tagFilters.map((tag) => (
              <li key={tag}>
                <button
                  type="button"
                  className="filter-chip"
                  onClick={() => {
                    const next = tagFilters.filter((v) => v !== tag);
                    update({ tag: next.length > 0 ? next : undefined });
                  }}
                >
                  {tag.slice(tag.indexOf(":") + 1)}
                  <X size={14} aria-label={t("library.removeFilter")} />
                </button>
              </li>
            ))}
            {activeFilters.map((key) => (
              <li key={key}>
                <button
                  type="button"
                  className="filter-chip"
                  onClick={() => update({ [key]: undefined })}
                >
                  {key === "date_from" || key === "date_to"
                    ? `${t(key === "date_from" ? "library.from" : "library.to")} ${search[key]}`
                    : valueLabel(t, key, String(search[key]))}
                  <X size={14} aria-label={t("library.removeFilter")} />
                </button>
              </li>
            ))}
          </ul>
        </div>

        {hybrid && Object.keys(skipped).length > 0 ? (
          <p className="library__note" role="status">
            {t("search.degraded")}
          </p>
        ) : null}

        <div className="library__scroll" ref={scrollRef}>
          {hybrid ? (
            found.isLoading ? (
              <div className="skeleton-grid" aria-busy="true">
                {Array.from({ length: 8 }, (_, i) => (
                  <Skeleton key={i} height={180} radius={16} />
                ))}
              </div>
            ) : found.isError ? (
              <EmptyState
                title={t("library.errorTitle")}
                description={t("library.errorText")}
                action={<Button onClick={() => void found.refetch()}>{t("common.retry")}</Button>}
              />
            ) : hits.length === 0 ? (
              <EmptyState
                icon={<FolderSearch size={28} />}
                title={t("library.emptyTitle")}
                description={t("library.emptyFiltered")}
              />
            ) : (
              <>
                <ul className="hit-grid" aria-label={t("library.results")}>
                  {hits.map((hit) => (
                    <HitCard key={`${hit.asset.id}-${hit.shot?.id ?? ""}`} hit={hit} />
                  ))}
                </ul>
                {found.hasNextPage ? (
                  <Button
                    variant="secondary"
                    loading={found.isFetchingNextPage}
                    onClick={() => void found.fetchNextPage()}
                  >
                    {t("search.more")}
                  </Button>
                ) : null}
              </>
            )
          ) : assets.isLoading ? (
            <div className="skeleton-grid" aria-busy="true">
              {Array.from({ length: 12 }, (_, i) => (
                <div key={i}>
                  <Skeleton
                    style={{ aspectRatio: "16 / 9", width: "100%", height: "auto" }}
                    radius={16}
                  />
                  <Skeleton height={14} width="70%" style={{ marginTop: 12 }} />
                </div>
              ))}
            </div>
          ) : assets.isError ? (
            <EmptyState
              title={t("library.errorTitle")}
              description={t("library.errorText")}
              action={<Button onClick={() => void assets.refetch()}>{t("common.retry")}</Button>}
            />
          ) : items.length === 0 ? (
            <EmptyState
              icon={<FolderSearch size={28} />}
              title={t("library.emptyTitle")}
              description={
                activeFilters.length > 0 || search.q
                  ? t("library.emptyFiltered")
                  : t("library.emptyFresh")
              }
              action={
                activeFilters.length > 0 || search.q ? (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setText("");
                      update({
                        q: undefined,
                        ...(Object.fromEntries(
                          FILTER_KEYS.map((k) => [k, undefined]),
                        ) as Partial<LibrarySearch>),
                      });
                    }}
                  >
                    {t("library.resetSearch")}
                  </Button>
                ) : undefined
              }
            />
          ) : view === "grid" ? (
            <AssetGrid
              items={items}
              scrollRef={scrollRef}
              selected={selected}
              onToggle={toggle}
              onOpen={openAsset}
              onNearEnd={onNearEnd}
            />
          ) : (
            <DataTable
              label={t("library.results")}
              rows={items}
              columns={columns}
              getRowId={(a) => a.id}
              rowHeight={56}
              maxHeight={listHeight}
              onRowClick={openAsset}
              onNearEnd={onNearEnd}
            />
          )}
        </div>

        {selected.size > 0 ? (
          <div className="bulkbar" role="region" aria-label={t("library.bulk")}>
            <strong>{t("library.selected", { count: selected.size })}</strong>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setSelected(new Set(items.map((a) => a.id)))}
            >
              {t("library.selectLoaded")}
            </Button>
            <Select
              aria-label={t("library.addToCollection")}
              placeholder={t("library.addToCollection")}
              value=""
              onValueChange={(collection) =>
                bulk.mutate(
                  { asset_ids: [...selected], add_to_collection: collection },
                  { onSuccess: () => setSelected(new Set()) },
                )
              }
              options={(collections.data ?? []).map((c) => ({ value: c.id, label: c.name }))}
            />
            <Button
              size="sm"
              variant="secondary"
              icon={<ListPlus size={14} />}
              onClick={() =>
                bulk.mutate(
                  { asset_ids: [...selected], locked: true },
                  { onSuccess: () => setSelected(new Set()) },
                )
              }
            >
              {t("library.lockSelected")}
            </Button>
            <BulkPrivacy ids={[...selected]} onDone={() => setSelected(new Set())} />
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
              {t("library.clearSelection")}
            </Button>
          </div>
        ) : null}
      </section>
    </div>
  );
}
