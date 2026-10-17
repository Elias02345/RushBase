import { Card, EmptyState, Skeleton } from "@rushbase/ui";
import { useTranslation } from "react-i18next";

import { useThroughput } from "../../api/finalQueries";
import { formatNumber } from "../../lib/format";

/** Finished stage jobs per hour over the last seven days. */
export function ThroughputCard() {
  const { t } = useTranslation();
  const flow = useThroughput();
  const hours = flow.data?.hours ?? [];
  const peak = Math.max(1, ...hours.map((h) => h.count));
  const busiest = hours.reduce(
    (best, h) => (h.count > best.count ? h : best),
    hours[0] ?? { count: 0, hour: "" },
  );
  return (
    <Card title={t("dashboard.throughput.title")} className="dash-wide">
      {flow.isLoading ? (
        <Skeleton height={120} />
      ) : !flow.data || flow.data.total === 0 ? (
        <EmptyState
          compact
          title={t("dashboard.throughput.empty")}
          description={t("dashboard.throughput.emptyText")}
        />
      ) : (
        <div className="throughput">
          <p className="throughput__summary">
            <strong>
              {t("dashboard.throughput.average", {
                n: Math.round(flow.data.per_hour_avg * 10) / 10,
              })}
            </strong>
            <span className="muted">
              {" "}
              {t("dashboard.throughput.total", { count: formatNumber(flow.data.total) })}
              {busiest.count > 0
                ? ` · ${t("dashboard.throughput.peak", { count: busiest.count })}`
                : ""}
            </span>
          </p>
          <div
            className="throughput__bars"
            role="img"
            aria-label={t("dashboard.throughput.label", {
              total: flow.data.total,
              average: Math.round(flow.data.per_hour_avg * 10) / 10,
            })}
          >
            {hours.map((h) => (
              <span
                key={h.hour}
                title={`${new Date(h.hour).toLocaleString()}: ${h.count}`}
                style={{ height: `${Math.max(2, (h.count / peak) * 100)}%` }}
                className={
                  h.count === 0 ? "throughput__bar throughput__bar--idle" : "throughput__bar"
                }
              />
            ))}
          </div>
          <p className="muted throughput__axis">
            <span>{t("dashboard.throughput.daysAgo", { count: 7 })}</span>
            <span>{t("dashboard.throughput.now")}</span>
          </p>
        </div>
      )}
    </Card>
  );
}
