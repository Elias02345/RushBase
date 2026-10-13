import { observeElementRect, useVirtualizer } from "@tanstack/react-virtual";
import { useRef, type KeyboardEvent, type ReactNode } from "react";

import { cx } from "./cx";

export interface DataColumn<T> {
  key: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  /** CSS grid track, for example `"120px"` or `"2fr"`; defaults to `minmax(0, 1fr)`. */
  width?: string;
  align?: "start" | "end";
}

export interface DataTableProps<T> {
  rows: readonly T[];
  columns: readonly DataColumn<T>[];
  getRowId: (row: T) => string;
  /** Accessible name of the table. */
  label: string;
  rowHeight?: number;
  /** Maximum height of the scroll area; the table shrinks to its rows below that. */
  maxHeight?: number;
  onRowClick?: (row: T) => void;
  selectedId?: string | null;
  empty?: ReactNode;
  className?: string;
}

/**
 * Virtualised table: only the visible rows are in the DOM, so 100k rows stay smooth.
 * Built from ARIA table roles on a CSS grid so columns align without a real `<table>`.
 */
export function DataTable<T>({
  rows,
  columns,
  getRowId,
  label,
  rowHeight = 52,
  maxHeight = 520,
  onRowClick,
  selectedId,
  empty,
  className,
}: DataTableProps<T>) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 8,
    initialRect: { width: 800, height: maxHeight },
    // An unmeasurable (zero-height) container, such as a hidden tab or jsdom, falls back to the
    // initial size so rows still render instead of an empty table.
    observeElementRect: (instance, callback) =>
      observeElementRect(instance, (rect) =>
        callback(rect.height === 0 ? { width: 800, height: maxHeight } : rect),
      ),
  });
  const template = columns.map((column) => column.width ?? "minmax(0, 1fr)").join(" ");
  const bodyHeight = rows.length * rowHeight;

  if (rows.length === 0 && empty) return <div className={className}>{empty}</div>;

  const handleKey = (event: KeyboardEvent<HTMLDivElement>, row: T) => {
    if (onRowClick && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      onRowClick(row);
    }
  };

  return (
    <div
      role="table"
      aria-label={label}
      aria-rowcount={rows.length + 1}
      className={cx("rb-table", className)}
    >
      <div role="rowgroup" className="rb-table__head">
        <div role="row" className="rb-table__row" style={{ gridTemplateColumns: template }}>
          {columns.map((column) => (
            <div
              key={column.key}
              role="columnheader"
              className={cx("rb-table__cell", column.align === "end" && "rb-table__cell--end")}
            >
              {column.header}
            </div>
          ))}
        </div>
      </div>
      <div
        ref={scrollRef}
        role="rowgroup"
        className="rb-table__scroll"
        style={{ maxHeight, height: Math.min(bodyHeight, maxHeight) }}
      >
        <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
          {virtualizer.getVirtualItems().map((item) => {
            const row = rows[item.index];
            if (row === undefined) return null;
            const id = getRowId(row);
            return (
              <div
                key={id}
                role="row"
                aria-rowindex={item.index + 2}
                aria-selected={selectedId === undefined ? undefined : selectedId === id}
                tabIndex={onRowClick ? 0 : undefined}
                className={cx(
                  "rb-table__row rb-table__row--body",
                  onRowClick && "rb-table__row--interactive",
                  selectedId === id && "rb-table__row--selected",
                )}
                style={{
                  gridTemplateColumns: template,
                  height: item.size,
                  transform: `translateY(${item.start}px)`,
                }}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                onKeyDown={(event) => handleKey(event, row)}
              >
                {columns.map((column) => (
                  <div
                    key={column.key}
                    role="cell"
                    className={cx(
                      "rb-table__cell",
                      column.align === "end" && "rb-table__cell--end",
                    )}
                  >
                    {column.cell(row)}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
