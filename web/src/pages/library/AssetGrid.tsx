import { AssetTile, StatusChip } from "@rushbase/ui";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import { useTranslation } from "react-i18next";

import type { AssetSummary } from "../../api/contracts";
import { formatDate, formatDuration, formatResolution } from "../../lib/format";
import { assetTone } from "../../lib/status";

const MIN_TILE = 232;
const GAP = 20;
const META_HEIGHT = 62;

export interface SelectModifiers {
  shift: boolean;
  additive: boolean;
}

interface AssetGridProps {
  items: AssetSummary[];
  scrollRef: RefObject<HTMLDivElement | null>;
  selected: ReadonlySet<string>;
  onToggle: (index: number, modifiers: SelectModifiers) => void;
  onOpen: (asset: AssetSummary) => void;
  onNearEnd: () => void;
}

export function tileFacts(asset: AssetSummary): string[] {
  const facts: string[] = [];
  const resolution = formatResolution(asset.width, asset.height);
  if (resolution) facts.push(resolution);
  if (asset.media_type === "video" && asset.fps) facts.push(`${Math.round(asset.fps)} fps`);
  return facts;
}

/** Virtualised, keyboard-navigable grid of asset tiles. */
export function AssetGrid({ items, scrollRef, selected, onToggle, onOpen, onNearEnd }: AssetGridProps) {
  const { t } = useTranslation();
  const innerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1000);
  const [focusIndex, setFocusIndex] = useState(0);
  const pendingFocus = useRef(false);

  useLayoutEffect(() => {
    const node = innerRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry && entry.contentRect.width > 0) setWidth(entry.contentRect.width);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const cols = Math.max(1, Math.floor((width + GAP) / (MIN_TILE + GAP)));
  const tileWidth = (width - GAP * (cols - 1)) / cols;
  const rowHeight = Math.round((tileWidth * 9) / 16 + META_HEIGHT + GAP);
  const rowCount = Math.ceil(items.length / cols);

  // eslint-disable-next-line react-hooks/incompatible-library -- virtual items are read each render
  const virtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 3,
    initialRect: { width, height: 800 },
  });
  useEffect(() => virtualizer.measure(), [rowHeight, virtualizer]);

  const rows = virtualizer.getVirtualItems();
  const lastRow = rows[rows.length - 1]?.index ?? -1;
  useEffect(() => {
    if (lastRow >= rowCount - 3) onNearEnd();
  }, [lastRow, rowCount, onNearEnd]);

  useEffect(() => {
    if (!pendingFocus.current) return;
    const target = innerRef.current?.querySelector<HTMLElement>(
      `[data-index="${focusIndex}"] .rb-tile__media`,
    );
    if (target) {
      pendingFocus.current = false;
      target.focus();
    }
  });

  const move = (event: KeyboardEvent<HTMLDivElement>) => {
    const keys: Record<string, number> = {
      ArrowRight: 1,
      ArrowLeft: -1,
      ArrowDown: cols,
      ArrowUp: -cols,
    };
    let next = focusIndex;
    if (event.key in keys) next = focusIndex + (keys[event.key] ?? 0);
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = items.length - 1;
    else return;
    event.preventDefault();
    next = Math.min(items.length - 1, Math.max(0, next));
    setFocusIndex(next);
    pendingFocus.current = true;
    virtualizer.scrollToIndex(Math.floor(next / cols), { align: "auto" });
  };

  return (
    <div
      ref={innerRef}
      className="asset-grid"
      role="group"
      aria-label={t("library.results")}
      onKeyDown={move}
      style={{ height: virtualizer.getTotalSize() }}
    >
      {rows.map((row) => (
        <div
          key={row.key}
          className="asset-grid__row"
          style={{
            gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
            columnGap: GAP,
            transform: `translateY(${row.start}px)`,
            height: row.size,
          }}
        >
          {items.slice(row.index * cols, row.index * cols + cols).map((asset, offset) => {
            const index = row.index * cols + offset;
            const name = asset.title ?? asset.filename;
            return (
              <div key={asset.id} data-index={index}>
                <AssetTile
                  title={name}
                  media={asset.media_type}
                  poster={asset.poster_url}
                  sprite={
                    asset.sprite_url && asset.sprite_tiles
                      ? { url: asset.sprite_url, ...asset.sprite_tiles }
                      : null
                  }
                  durationLabel={asset.duration_ms ? formatDuration(asset.duration_ms) : null}
                  facts={tileFacts(asset)}
                  meta={asset.camera_model ?? formatDate(asset.capture_at)}
                  status={
                    <StatusChip tone={assetTone(asset.status)}>
                      {t(`assetStatus.${asset.status}`)}
                    </StatusChip>
                  }
                  selected={selected.has(asset.id)}
                  selectLabel={t("library.select", { name })}
                  tabIndex={index === focusIndex ? 0 : -1}
                  onOpen={() => onOpen(asset)}
                  onToggleSelect={(modifiers) => {
                    setFocusIndex(index);
                    onToggle(index, modifiers);
                  }}
                />
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
