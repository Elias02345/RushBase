import { Badge, Button, Card, ProgressRing } from "@rushbase/ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { fetchHealth } from "./api";
import { readiness } from "./readiness";

export function HealthCard() {
  const { t } = useTranslation();
  const { data, isError, isFetching, refetch } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 10_000,
    retry: false,
  });

  const serverUp = data !== undefined;
  const databaseUp = data?.database === true;
  const percent = readiness(serverUp, databaseUp);

  return (
    <Card title={t("health.title")}>
      <div className="health">
        <ProgressRing value={percent} label={t("health.readiness")} size={120} strokeWidth={12} />
        <ul className="health__list">
          <li>
            <span>{t("health.server")}</span>
            <Badge tone={serverUp ? "success" : isError ? "danger" : "neutral"}>
              {serverUp ? t("health.up") : isError ? t("health.down") : t("health.checking")}
            </Badge>
          </li>
          <li>
            <span>{t("health.database")}</span>
            <Badge tone={databaseUp ? "success" : serverUp ? "warning" : "neutral"}>
              {databaseUp ? t("health.dbUp") : serverUp ? t("health.dbDown") : "–"}
            </Badge>
          </li>
          {data ? (
            <li className="health__version">{t("health.version", { version: data.version })}</li>
          ) : null}
        </ul>
      </div>
      <Button variant="secondary" size="sm" onClick={() => void refetch()} disabled={isFetching}>
        {isFetching ? t("health.checking") : t("health.refresh")}
      </Button>
    </Card>
  );
}
