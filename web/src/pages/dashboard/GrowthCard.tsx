import { Card, EmptyState, Skeleton } from "@rushbase/ui";
import { useTranslation } from "react-i18next";

import type { GrowthMonth } from "../../api/contracts";
import { useGrowth } from "../../api/finalQueries";
import { formatBytes } from "../../lib/format";

const MEDIA_ORDER = ["video", "photo", "audio", "other"];

function typesOf(months: GrowthMonth[]): string[] {
  const present = new Set(months.flatMap((m) => Object.keys(m.by_media_type)));
  return [
    ...MEDIA_ORDER.filter((k) => present.has(k)),
    ...[...present].filter((k) => !MEDIA_ORDER.includes(k)),
  ];
}

/** Storage added per month, stacked by media type; the running total is in the summary. */
export function GrowthCard() {
  const { t } = useTranslation();
  const growth = useGrowth();
  const months = (growth.data ?? []).slice(-24);
  const types = typesOf(months);
  const peak = Math.max(1, ...months.map((m) => m.bytes));
  const total = (growth.data ?? []).reduce((sum, m) => sum + m.bytes, 0);
  const summary = months.map((m) => `${m.month}: ${formatBytes(m.bytes)}`).join(", ");
  return (
    <Card title={t("dashboard.growth.title")} className="dash-wide">
      {growth.isLoading ? (
        <Skeleton height={160} />
      ) : months.length === 0 ? (
        <EmptyState
          compact
          title={t("dashboard.growth.empty")}
          description={t("dashboard.growth.emptyText")}
        />
      ) : (
        <div className="growth">
          <p className="growth__total">
            <strong>{formatBytes(total)}</strong>
            <span className="muted">
              {" "}
              {t("dashboard.growth.total", { months: growth.data?.length ?? 0 })}
            </span>
          </p>
          <div
            className="growth__bars"
            role="img"
            aria-label={t("dashboard.growth.label", { summary })}
          >
            {months.map((m) => (
              <div
                key={m.month}
                className="growth__col"
                title={`${m.month}: ${formatBytes(m.bytes)} · ${t("dashboard.growth.assets", { count: m.count })}`}
              >
                <div className="growth__stack" style={{ height: `${(m.bytes / peak) * 100}%` }}>
                  {types.map((type, index) => {
                    const bucket = m.by_media_type[type];
                    return bucket ? (
                      <span
                        key={type}
                        style={{
                          flexGrow: bucket.bytes,
                          background: `var(--rb-seg-${index % 4})`,
                        }}
                      />
                    ) : null;
                  })}
                </div>
                <span className="growth__month" aria-hidden="true">
                  {m.month.slice(2)}
                </span>
              </div>
            ))}
          </div>
          <ul className="legend">
            {types.map((type, index) => (
              <li key={type}>
                <i style={{ background: `var(--rb-seg-${index % 4})` }} aria-hidden="true" />
                {t(`media.${type}`, { defaultValue: type })}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
