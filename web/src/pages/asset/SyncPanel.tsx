import { Button, EmptyState, StatusChip, useToast } from "@rushbase/ui";
import { Link } from "@tanstack/react-router";
import { AudioLines } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { AssetDetail } from "../../api/contracts";
import { useDecideSync } from "../../api/safetyQueries";

function formatOffset(ms: number): string {
  const sign = ms < 0 ? "-" : "+";
  return `${sign}${(Math.abs(ms) / 1000).toFixed(2)} s`;
}

/** Audio/video sync suggestions: nothing is linked until a person confirms. */
export function SyncPanel({ detail, canEdit }: { detail: AssetDetail; canEdit: boolean }) {
  const { t } = useTranslation();
  const toast = useToast();
  const decide = useDecideSync();
  const rows = detail.sync ?? [];
  if (rows.length === 0) {
    return (
      <EmptyState
        compact
        icon={<AudioLines size={24} />}
        title={t("sync.emptyTitle")}
        description={t("sync.emptyText")}
      />
    );
  }
  const send = (id: string, decision: "confirmed" | "rejected") =>
    decide.mutate(
      { id, decision },
      { onError: () => toast.push({ title: t("common.error"), tone: "error" }) },
    );
  return (
    <div>
      <p className="muted">{t("sync.lead")}</p>
      <ul className="sync-list">
        {rows.map((row) => (
          <li key={row.id} className="sync-list__item">
            <div>
              <Link to="/assets/$id" params={{ id: row.other_asset_id }} className="link">
                {row.other_filename}
              </Link>{" "}
              <span className="muted">
                {t(`sync.role.${row.other_role}`, { defaultValue: row.other_role })}
              </span>
              <p className="muted">
                {t("sync.details", {
                  offset: formatOffset(row.offset_ms),
                  confidence: Math.round(row.confidence * 100),
                  basis: t(`sync.basis.${row.basis}`, { defaultValue: row.basis }),
                })}
              </p>
            </div>
            <div className="sync-list__actions">
              <StatusChip tone={row.status === "confirmed" ? "filed" : "neutral"} plain>
                {t(`sync.status.${row.status}`)}
              </StatusChip>
              {canEdit && row.status !== "confirmed" ? (
                <Button size="sm" onClick={() => send(row.id, "confirmed")}>
                  {t("sync.confirm")}
                </Button>
              ) : null}
              {canEdit && row.status !== "rejected" ? (
                <Button size="sm" variant="ghost" onClick={() => send(row.id, "rejected")}>
                  {t("sync.reject")}
                </Button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
