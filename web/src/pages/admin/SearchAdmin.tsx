import { Button, Skeleton, StatusChip, useToast } from "@rushbase/ui";
import { FlaskConical, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useEvalRuns, useReembed, useRunEval, useSearchCapabilities } from "../../api/aiQueries";
import type { SearchCapabilities } from "../../api/contracts";
import { formatDateTime } from "../../lib/format";

function Channel({ name, space }: { name: string; space: SearchCapabilities["text_semantic"] }) {
  const { t } = useTranslation();
  return (
    <li>
      <div>
        <strong>{name}</strong>{" "}
        <StatusChip tone={space.available ? "filed" : "review"} plain>
          {space.available ? t("searchAdmin.available") : t("searchAdmin.unavailable")}
        </StatusChip>
        <p className="muted">
          {space.model_id || "—"}
          {space.revision ? ` @ ${space.revision}` : ""} ·{" "}
          {t("searchAdmin.vectors", {
            ok: space.vectors_compatible,
            bad: space.vectors_mismatched,
          })}
        </p>
        {space.reason ? <p className="muted">{space.reason}</p> : null}
      </div>
    </li>
  );
}

/** Search channels, vector compatibility with the server's encoder, and evaluation runs. */
export function SearchAdmin() {
  const { t } = useTranslation();
  const toast = useToast();
  const caps = useSearchCapabilities();
  const reembed = useReembed();
  const runs = useEvalRuns(true);
  const runEval = useRunEval();
  if (caps.isLoading) return <Skeleton height={200} radius={16} />;
  const data = caps.data;
  return (
    <div className="search-admin">
      <section aria-label={t("searchAdmin.channels")}>
        <h2>{t("searchAdmin.channels")}</h2>
        <ul className="search-admin__channels">
          <li>
            <strong>{t("searchAdmin.fulltext")}</strong>{" "}
            <StatusChip tone="filed" plain>
              {t("searchAdmin.available")}
            </StatusChip>
          </li>
          {data ? (
            <>
              <Channel name={t("searchAdmin.textChannel")} space={data.text_semantic} />
              <Channel name={t("searchAdmin.imageChannel")} space={data.image_semantic} />
            </>
          ) : null}
        </ul>
        {data?.reembed_needed ? (
          <div className="search-admin__notice" role="status">
            <p>{t("searchAdmin.reembedText")}</p>
            <Button
              icon={<RefreshCw size={16} />}
              loading={reembed.isPending}
              onClick={() =>
                reembed.mutate(undefined, {
                  onSuccess: (r) =>
                    toast.push({
                      title: t("searchAdmin.reembedQueued", { count: r.assets }),
                      tone: "success",
                    }),
                })
              }
            >
              {t("searchAdmin.reembed")}
            </Button>
          </div>
        ) : null}
        <p className="muted">{t("searchAdmin.byImage")}</p>
      </section>
      <section aria-label={t("searchAdmin.eval")}>
        <h2>{t("searchAdmin.eval")}</h2>
        <Button
          variant="secondary"
          icon={<FlaskConical size={16} />}
          loading={runEval.isPending}
          onClick={() => runEval.mutate()}
        >
          {t("searchAdmin.runEval")}
        </Button>
        <ul className="search-admin__runs">
          {(runs.data ?? []).map((run) => {
            const accuracy = (run.metrics as { overall?: { accuracy?: number | null } }).overall
              ?.accuracy;
            return (
              <li key={run.id}>
                <strong>{formatDateTime(run.created_at)}</strong>
                <span className="muted">
                  {t("searchAdmin.runSummary", {
                    labels: run.golden_count,
                    accuracy:
                      accuracy === null || accuracy === undefined
                        ? "—"
                        : `${Math.round(accuracy * 100)} %`,
                  })}
                </span>
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}
