import { Button, StatusChip, useToast } from "@rushbase/ui";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import type { AssetNeedLink } from "../../api/contracts";
import { useNeedActions } from "../../api/mcpQueries";

type Decision = "confirmed" | "rejected" | "candidate";

/** Needs items this asset is a candidate for, with the confirm / reject decision inline. */
export function NeedsPanel({
  assetId,
  links,
  canEdit,
}: {
  assetId: string;
  links: AssetNeedLink[];
  canEdit: boolean;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { decide } = useNeedActions();
  const send = (link: AssetNeedLink, status: Decision) =>
    decide.mutate(
      { item: link.item_id, asset: assetId, status },
      { onError: () => toast.push({ title: t("common.error"), tone: "error" }) },
    );
  const lists = new Map<string, { title: string; open: boolean; items: AssetNeedLink[] }>();
  for (const link of links) {
    const entry = lists.get(link.list_id) ?? {
      title: link.list_title,
      open: link.list_status === "open",
      items: [],
    };
    entry.items.push(link);
    lists.set(link.list_id, entry);
  }
  return (
    <div className="needs-panel">
      <p className="muted">{t("assetNeeds.lead")}</p>
      {[...lists.entries()].map(([id, list]) => (
        <section key={id} className="needs-panel__list" aria-label={list.title}>
          <h3 className="needs-panel__title">
            <Link to="/needs" search={{ list: id }} className="link">
              {list.title}
            </Link>
            {list.open ? null : (
              <StatusChip tone="neutral" plain>
                {t("assetNeeds.closed")}
              </StatusChip>
            )}
          </h3>
          <ul className="sync-list">
            {list.items.map((link) => (
              <li key={link.candidate_id} className="sync-list__item">
                <div>
                  <strong>{link.item_description}</strong>
                  <p className="muted">
                    {t("assetNeeds.itemState", {
                      state: t(`assetNeeds.item.${link.item_status}`),
                      score: Math.round(link.score * 100),
                    })}
                    {link.shot_idx !== null
                      ? ` · ${t("assetNeeds.shot", { n: link.shot_idx + 1 })}`
                      : ""}
                  </p>
                </div>
                <div className="sync-list__actions">
                  <StatusChip tone={link.status === "confirmed" ? "filed" : "neutral"} plain>
                    {t(`assetNeeds.status.${link.status}`)}
                  </StatusChip>
                  {canEdit && link.status !== "confirmed" ? (
                    <Button size="sm" onClick={() => send(link, "confirmed")}>
                      {t("assetNeeds.confirm")}
                    </Button>
                  ) : null}
                  {canEdit && link.status !== "rejected" ? (
                    <Button size="sm" variant="ghost" onClick={() => send(link, "rejected")}>
                      {t("assetNeeds.reject")}
                    </Button>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
