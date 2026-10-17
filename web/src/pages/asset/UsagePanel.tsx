import { Button, EmptyState, Input, StatusChip, useToast } from "@rushbase/ui";
import { Clapperboard, Plus, X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { AssetDetail } from "../../api/contracts";
import { useUsageActions } from "../../api/finalQueries";
import { formatDateTime } from "../../lib/format";

/** Projects the asset was used in: reported by Resolve, marked by hand or by Claude. */
export function UsagePanel({ detail, canEdit }: { detail: AssetDetail; canEdit: boolean }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { mark, unmark } = useUsageActions(detail.id);
  const [project, setProject] = useState("");
  const rows = detail.usage ?? [];
  const fail = () => toast.push({ title: t("common.error"), tone: "error" });
  return (
    <div className="usage-panel">
      <p className="muted">{t("usage.lead")}</p>
      {rows.length === 0 ? (
        <EmptyState
          compact
          icon={<Clapperboard size={24} />}
          title={t("usage.emptyTitle")}
          description={t("usage.emptyText")}
        />
      ) : (
        <ul className="usage-list">
          {rows.map((row) => (
            <li key={`${row.project_name}/${row.source}`} className="usage-list__item">
              <div>
                <strong>{row.project_name}</strong>
                <p className="muted">
                  {t("usage.seen", {
                    first: formatDateTime(row.first_seen),
                    last: formatDateTime(row.last_seen),
                  })}
                  {row.machine ? ` · ${row.machine}` : ""}
                </p>
              </div>
              <div className="usage-list__actions">
                <StatusChip tone={row.source === "resolve" ? "filed" : "neutral"} plain>
                  {t(`usage.source.${row.source}`)}
                </StatusChip>
                {canEdit && row.source === "manual" ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={<X size={14} />}
                    onClick={() =>
                      unmark.mutate(
                        { project_name: row.project_name, source: "manual" },
                        { onError: fail },
                      )
                    }
                  >
                    {t("usage.remove")}
                  </Button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
      {canEdit ? (
        <form
          className="inline-form usage-panel__add"
          onSubmit={(event) => {
            event.preventDefault();
            const name = project.trim();
            if (!name) return;
            mark.mutate(name, { onSuccess: () => setProject(""), onError: fail });
          }}
        >
          <Input
            aria-label={t("usage.projectName")}
            placeholder={t("usage.projectPlaceholder")}
            value={project}
            maxLength={300}
            onChange={(e) => setProject(e.target.value)}
          />
          <Button
            type="submit"
            variant="secondary"
            icon={<Plus size={16} />}
            loading={mark.isPending}
            disabled={project.trim() === ""}
          >
            {t("usage.add")}
          </Button>
        </form>
      ) : null}
    </div>
  );
}
