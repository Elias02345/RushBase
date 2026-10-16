import { AlertTriangle, Library, ShoppingBasket } from "lucide-react";
import { useDeferredValue, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  AssetTile,
  Button,
  EmptyState,
  SearchField,
  Select,
  Skeleton,
  StatusChip,
} from "@rushbase/ui";

import { derivedPath } from "../bridge";
import { useBridge } from "../bridge/context";
import type { AssetSummary } from "../bridge/types";
import { AssetDrawer } from "../components/AssetDrawer";
import { BasketPanel } from "../components/BasketPanel";
import { formatClock } from "../lib/format";
import { useBasket, useFacets, useLibrary } from "../state/library";
import { useOverview, useSettings } from "../state/queries";

const SORTS = ["-date", "date", "filename", "-size", "-duration"] as const;

function mediaKind(type: string): "video" | "photo" | "audio" | "other" {
  return type === "video" || type === "photo" || type === "audio" ? type : "other";
}

/** Library: search and filters via the server, tiles from local previews, detail with local playback. */
export function LibraryPage({ onSetup }: { onSetup: () => void }) {
  const { t, i18n } = useTranslation();
  const bridge = useBridge();
  const { data: settings } = useSettings();
  const overview = useOverview().data;
  const [text, setText] = useState("");
  const deferred = useDeferredValue(text);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<string>("-date");
  const [openId, setOpenId] = useState<string | null>(null);
  const [basketOpen, setBasketOpen] = useState(false);
  const { basket, add } = useBasket();
  const ready = !!overview && overview.server.reachable;
  const library = useLibrary({ text: deferred, filters, sort }, ready);
  const facets = useFacets(ready);
  const root = settings?.library_root ?? "";
  const items = library.data?.pages.flatMap((p) => p.items) ?? [];
  const total = library.data?.pages[0]?.total;
  const lang = i18n.language === "en" ? "label_en" : "label_de";

  if (overview && !ready) {
    return (
      <div className="dk-page">
        <EmptyState
          icon={<Library />}
          title={t("library.offlineTitle")}
          description={t("library.offlineText")}
          action={<Button onClick={onSetup}>{t("issue.startSetup")}</Button>}
        />
      </div>
    );
  }
  const authFailure = library.error && /token|401|login/i.test(library.error.message);

  const tile = (asset: AssetSummary) => {
    const posterPath = derivedPath(root, asset.poster_url);
    return (
      <AssetTile
        key={asset.id}
        title={asset.filename}
        media={mediaKind(asset.media_type)}
        poster={posterPath ? bridge.assetUrl(posterPath) : null}
        aspectRatio={asset.width && asset.height ? asset.width / asset.height : 16 / 9}
        durationLabel={formatClock(asset.duration_ms) || null}
        facts={[asset.height ? `${asset.height}p` : "", asset.fps ? `${asset.fps} fps` : ""].filter(
          Boolean,
        )}
        status={
          asset.review_state === "pending" ? (
            <StatusChip tone="review">{t("library.review")}</StatusChip>
          ) : undefined
        }
        meta={asset.summary}
        selected={basket.items.some((i) => i.asset_id === asset.id)}
        selectLabel={t("library.select", { name: asset.filename })}
        onOpen={() => setOpenId(asset.id)}
        onToggleSelect={() => void add(asset.id)}
      />
    );
  };

  return (
    <div className="dk-page dk-library">
      <div className="dk-toolbar">
        <SearchField
          value={text}
          onValueChange={setText}
          clearLabel={t("common.clear")}
          placeholder={t("library.search")}
          aria-label={t("library.search")}
          variant="lg"
        />
        <Select
          aria-label={t("library.sort")}
          value={sort}
          onValueChange={setSort}
          options={SORTS.map((s) => ({ value: s, label: t(`library.sorts.${s}`) }))}
        />
        <Button
          variant="secondary"
          icon={<ShoppingBasket size={16} />}
          onClick={() => setBasketOpen(true)}
        >
          {t("basket.button", { count: basket.count })}
        </Button>
      </div>
      <div className="dk-filters">
        {(facets.data ?? []).slice(0, 4).map((facet) => (
          <Select
            key={facet.key}
            aria-label={facet[lang]}
            value={filters[facet.key] ?? "__all"}
            onValueChange={(value) =>
              setFilters((f) => ({ ...f, [facet.key]: value === "__all" ? "" : value }))
            }
            options={[
              { value: "__all", label: `${facet[lang]}: ${t("library.all")}` },
              ...(facet.values ?? []).map((v) => ({
                value: v.value,
                label: `${v.value} (${v.count})`,
              })),
            ]}
          />
        ))}
        {total != null ? (
          <span className="dk-muted">{t("library.count", { count: total })}</span>
        ) : null}
      </div>

      {library.isPending ? (
        <div className="dk-tiles" aria-busy="true">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} height={170} />
          ))}
        </div>
      ) : authFailure ? (
        <EmptyState
          icon={<AlertTriangle />}
          title={t("library.loginNeeded")}
          description={t("library.loginNeededText")}
          action={<Button onClick={onSetup}>{t("issue.startSetup")}</Button>}
        />
      ) : library.error ? (
        <p role="alert" className="dk-error">
          {library.error.message}
        </p>
      ) : items.length === 0 ? (
        <EmptyState
          icon={<Library />}
          title={t("library.empty")}
          description={t("library.emptyText")}
          compact
        />
      ) : (
        <>
          <div className="dk-tiles">{items.map(tile)}</div>
          {library.hasNextPage ? (
            <div className="dk-actions dk-actions--center">
              <Button
                variant="secondary"
                loading={library.isFetchingNextPage}
                onClick={() => void library.fetchNextPage()}
              >
                {t("library.more")}
              </Button>
            </div>
          ) : null}
        </>
      )}

      <AssetDrawer
        assetId={openId}
        onClose={() => setOpenId(null)}
        inBasket={basket.items.some((i) => i.asset_id === openId)}
        onAdd={(id, shots) => void add(id, shots)}
      />
      <BasketPanel open={basketOpen} onOpenChange={setBasketOpen} />
    </div>
  );
}
