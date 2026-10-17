import { Button, Dialog, Field, Input, Select, Skeleton, StatusChip, useToast } from "@rushbase/ui";
import { RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { ByModelResult, ModelUse, ReprocessField, ReprocessOp } from "../../api/contracts";
import { useReprocessByModel, useReprocessModels } from "../../api/finalQueries";
import { formatNumber } from "../../lib/format";

const FIELDS: ReprocessField[] = ["model_version", "model_id", "profile"];
const OPS: ReprocessOp[] = ["lt", "ne"];

function attribute(use: ModelUse, field: ReprocessField): string | null {
  return use[field] ?? null;
}

/** "Neu verarbeiten": queue a stage again by the model that produced its latest result. */
export function ReprocessDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const models = useReprocessModels(open);
  const run = useReprocessByModel();
  const [stage, setStage] = useState("");
  const [field, setField] = useState<ReprocessField>("model_version");
  const [op, setOp] = useState<ReprocessOp>("lt");
  const [value, setValue] = useState("");
  const [preview, setPreview] = useState<{ key: string; result: ByModelResult } | null>(null);

  const stages = useMemo(
    () => [...new Set((models.data ?? []).map((m) => m.stage))],
    [models.data],
  );
  const activeStage = stage || stages[0] || "";
  const key = `${activeStage}|${field}|${op}|${value.trim()}`;
  const current = preview?.key === key ? preview.result : null;
  const ready = activeStage !== "" && value.trim() !== "";
  const request = (dry_run: boolean) => ({
    stage: activeStage as ModelUse["stage"],
    field,
    op,
    value: value.trim(),
    dry_run,
  });
  const fail = () => toast.push({ title: t("common.error"), tone: "error" });

  const count = () =>
    run.mutate(request(true), {
      onSuccess: (result) => setPreview({ key, result }),
      onError: fail,
    });
  const queue = () =>
    run.mutate(request(false), {
      onSuccess: (result) => {
        toast.push({ title: t("reprocess.queued", { count: result.queued }), tone: "success" });
        setPreview(null);
        onOpenChange(false);
      },
      onError: fail,
    });

  const grouped = useMemo(() => {
    const byStage = new Map<string, ModelUse[]>();
    for (const use of models.data ?? [])
      byStage.set(use.stage, [...(byStage.get(use.stage) ?? []), use]);
    return [...byStage.entries()];
  }, [models.data]);

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("reprocess.title")}
      description={t("reprocess.lead")}
      closeLabel={t("common.close")}
      size="lg"
      footer={
        <>
          <Button
            variant="secondary"
            disabled={!ready}
            loading={run.isPending && run.variables?.dry_run === true}
            onClick={count}
          >
            {t("reprocess.count")}
          </Button>
          <Button
            icon={<RefreshCw size={16} />}
            disabled={!current || current.matched === 0}
            loading={run.isPending && run.variables?.dry_run === false}
            onClick={queue}
          >
            {current ? t("reprocess.queue", { count: current.matched }) : t("reprocess.queueNone")}
          </Button>
        </>
      }
    >
      <section aria-label={t("reprocess.current")} className="reprocess-models">
        <h3 className="reprocess-models__title">{t("reprocess.current")}</h3>
        {models.isLoading ? <Skeleton height={120} /> : null}
        {!models.isLoading && grouped.length === 0 ? (
          <p className="muted">{t("reprocess.noResults")}</p>
        ) : null}
        {grouped.map(([name, uses]) => (
          <div key={name} className="reprocess-models__stage">
            <h4>{t(`stages.${name}`, { defaultValue: name })}</h4>
            <ul className="plain-list plain-list--rows">
              {uses.map((use) => (
                <li key={`${use.model_id}|${use.model_version}|${use.profile}`}>
                  <span className="plain-list__main">
                    <strong>{use.model_id ?? "–"}</strong>
                    <span className="muted">
                      {t("reprocess.versionProfile", {
                        version: use.model_version ?? "–",
                        profile: use.profile ?? "–",
                      })}
                    </span>
                  </span>
                  <span className="reprocess-models__actions">
                    <StatusChip tone="neutral" plain>
                      {t("reprocess.assets", { count: use.assets })}
                    </StatusChip>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={attribute(use, field) === null}
                      onClick={() => {
                        setStage(use.stage);
                        setOp("ne");
                        setValue(attribute(use, field) ?? "");
                      }}
                    >
                      {t("reprocess.allButThis")}
                    </Button>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>
      <div className="reprocess-form">
        <Field label={t("reprocess.stage")}>
          {({ id }) => (
            <Select
              id={id}
              value={activeStage}
              onValueChange={setStage}
              options={stages.map((name) => ({
                value: name,
                label: t(`stages.${name}`, { defaultValue: name }),
              }))}
            />
          )}
        </Field>
        <Field label={t("reprocess.field")}>
          {({ id }) => (
            <Select
              id={id}
              value={field}
              onValueChange={(next) => setField(next as ReprocessField)}
              options={FIELDS.map((name) => ({
                value: name,
                label: t(`reprocess.fields.${name}`),
              }))}
            />
          )}
        </Field>
        <Field label={t("reprocess.operator")}>
          {({ id }) => (
            <Select
              id={id}
              value={op}
              onValueChange={(next) => setOp(next as ReprocessOp)}
              options={OPS.map((name) => ({ value: name, label: t(`reprocess.ops.${name}`) }))}
            />
          )}
        </Field>
        <Field label={t("reprocess.value")}>
          {(control) => (
            <Input
              {...control}
              value={value}
              maxLength={200}
              placeholder={t("reprocess.valuePlaceholder")}
              onChange={(e) => setValue(e.target.value)}
            />
          )}
        </Field>
      </div>
      {current ? (
        <div className="reprocess-result" role="status">
          <strong>{t("reprocess.matches", { count: current.matched })}</strong>
          {current.truncated ? <span className="muted"> {t("reprocess.truncated")}</span> : null}
          <ul className="plain-list">
            {current.by_value.map((entry) => (
              <li key={entry.value ?? "none"}>
                <code>{entry.value ?? t("reprocess.noValue")}</code>
                <span className="muted"> · {formatNumber(entry.assets)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Dialog>
  );
}
