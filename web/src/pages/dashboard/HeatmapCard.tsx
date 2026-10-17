import { Card, EmptyState, Field, Select, Skeleton } from "@rushbase/ui";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { HeatDimension } from "../../api/contracts";
import { useHeatDimensions, useHeatmap } from "../../api/finalQueries";
import { formatNumber } from "../../lib/format";
import { valueLabel } from "../../lib/facets";
import { filterFor } from "../../lib/heatmap";

const DEFAULT_ROWS = "media_type";
const DEFAULT_COLUMNS = "orientation";

/** Field x field coverage: every cell opens the library filtered to that combination. */
export function HeatmapCard() {
  const { t, i18n } = useTranslation();
  const dimensions = useHeatDimensions();
  const [rowsKey, setRowsKey] = useState(DEFAULT_ROWS);
  const [columnsKey, setColumnsKey] = useState(DEFAULT_COLUMNS);
  const heat = useHeatmap(rowsKey, columnsKey);
  const list = dimensions.data ?? [];
  const label = (d: HeatDimension) => (i18n.language === "en" ? d.label_en : d.label_de);
  const rowDim = list.find((d) => d.key === rowsKey);
  const colDim = list.find((d) => d.key === columnsKey);
  const options = list.map((d) => ({ value: d.key, label: label(d) }));
  const data = heat.data;
  const counts = new Map((data?.cells ?? []).map((c) => [`${c.row}\u0000${c.col}`, c.count]));
  const valueText = (dim: HeatDimension, value: string) =>
    dim.kind === "taxonomy" ? value : valueLabel(t, dim.key, value);
  const link = (row: string, col: string) =>
    rowDim && colDim ? filterFor(filterFor({}, rowDim, row), colDim, col) : {};

  return (
    <Card title={t("dashboard.coverage.title")} className="dash-wide">
      <p className="muted">{t("dashboard.coverage.lead")}</p>
      <div className="heat-controls">
        <Field label={t("dashboard.coverage.rows")}>
          {({ id }) => (
            <Select id={id} value={rowsKey} onValueChange={setRowsKey} options={options} />
          )}
        </Field>
        <Field label={t("dashboard.coverage.columns")}>
          {({ id }) => (
            <Select id={id} value={columnsKey} onValueChange={setColumnsKey} options={options} />
          )}
        </Field>
      </div>
      {!data || !rowDim || !colDim ? (
        <Skeleton height={140} />
      ) : data.row_values.length === 0 || data.column_values.length === 0 ? (
        <EmptyState
          compact
          title={t("dashboard.coverage.empty")}
          description={t("dashboard.coverage.emptyText")}
        />
      ) : (
        <>
          <div className="heat-scroll">
            <table className="heat">
              <caption className="visually-hidden">
                {t("dashboard.coverage.caption", { rows: label(rowDim), columns: label(colDim) })}
              </caption>
              <thead>
                <tr>
                  <th scope="col" className="heat__corner" />
                  {data.column_values.map((col) => (
                    <th key={col} scope="col">
                      {valueText(colDim, col)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.row_values.map((row) => (
                  <tr key={row}>
                    <th scope="row">{valueText(rowDim, row)}</th>
                    {data.column_values.map((col) => {
                      const count = counts.get(`${row}\u0000${col}`) ?? 0;
                      const strength =
                        data.max > 0
                          ? Math.round((count / data.max) * 26) + (count > 0 ? 10 : 0)
                          : 0;
                      return (
                        <td key={col}>
                          <Link
                            to="/library"
                            search={link(row, col)}
                            className={count === 0 ? "heat__cell heat__cell--gap" : "heat__cell"}
                            style={{
                              background:
                                count === 0
                                  ? undefined
                                  : `color-mix(in oklch, var(--rb-seg-0) ${strength}%, transparent)`,
                            }}
                            aria-label={t("dashboard.coverage.cell", {
                              row: valueText(rowDim, row),
                              column: valueText(colDim, col),
                              count,
                            })}
                          >
                            {formatNumber(count)}
                          </Link>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.truncated ? <p className="muted">{t("dashboard.coverage.truncated")}</p> : null}
          {data.weakest.length > 0 ? (
            <section className="heat-weakest" aria-label={t("dashboard.coverage.weakest")}>
              <h3>{t("dashboard.coverage.weakest")}</h3>
              <ul className="plain-list plain-list--rows">
                {data.weakest.map((cell) => (
                  <li key={`${cell.row}\u0000${cell.col}`}>
                    <Link to="/library" search={link(cell.row, cell.col)} className="link">
                      {valueText(rowDim, cell.row)} × {valueText(colDim, cell.col)}
                    </Link>
                    <span className="muted">
                      {t("dashboard.coverage.assets", { count: cell.count })}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </>
      )}
    </Card>
  );
}
