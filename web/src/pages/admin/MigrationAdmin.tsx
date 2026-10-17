import { Button, Card, Field, Input, Select, Switch, useToast } from "@rushbase/ui";
import { Download } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../../api/client";
import { useBatches } from "../../api/queries";
import {
  useMigrationExecute,
  useMigrationPreview,
  useMigrationSettings,
  useSetMigrationEnabled,
  type MigrationPreview,
  type MigrationResult,
} from "../../api/safetyQueries";

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

function Tool() {
  const { t } = useTranslation();
  const toast = useToast();
  const batches = useBatches();
  const preview = useMigrationPreview();
  const execute = useMigrationExecute();
  const [batch, setBatch] = useState("");
  const [template, setTemplate] = useState("");
  const [plan, setPlan] = useState<MigrationPreview | null>(null);
  const [typed, setTyped] = useState("");
  const [result, setResult] = useState<MigrationResult | null>(null);
  const request = { batch_id: batch, template };
  const error = (preview.error ?? execute.error) as unknown;

  return (
    <Card title={t("migration.tool")}>
      <form
        className="form-grid"
        onSubmit={(event) => {
          event.preventDefault();
          setResult(null);
          setTyped("");
          preview.mutate(request, { onSuccess: setPlan });
        }}
      >
        <Field label={t("migration.batch")}>
          {({ id }) => (
            <Select
              id={id}
              value={batch}
              placeholder={t("migration.batchPlaceholder")}
              onValueChange={(value) => {
                setBatch(value);
                setPlan(null);
              }}
              options={(batches.data ?? []).map((b) => ({ value: b.id, label: b.label }))}
            />
          )}
        </Field>
        <Field label={t("migration.template")} hint={t("migration.templateHint")}>
          {(c) => (
            <Input
              {...c}
              className="rb-input--mono"
              spellCheck={false}
              value={template}
              onChange={(e) => {
                setTemplate(e.target.value);
                setPlan(null);
              }}
            />
          )}
        </Field>
        <div>
          <Button
            type="submit"
            variant="secondary"
            disabled={!batch || !template}
            loading={preview.isPending}
          >
            {t("migration.preview")}
          </Button>
        </div>
      </form>
      {error instanceof ApiError ? (
        <p className="form-error" role="alert">
          {error.message}
        </p>
      ) : null}
      {plan ? (
        <>
          <p className="muted">{t("migration.summary", { count: plan.to_move })}</p>
          <ul className="preview-list">
            {plan.items.slice(0, 50).map((item) => (
              <li key={item.asset_ids.join()}>
                <code>{item.old_dir}</code>
                <span aria-hidden="true">→</span>
                <code>
                  {item.skipped ? t("migration.skipped", { reason: item.skipped }) : item.new_dir}
                </code>
              </li>
            ))}
          </ul>
          {plan.to_move > 0 ? (
            <form
              className="form-grid"
              onSubmit={(event) => {
                event.preventDefault();
                execute.mutate(
                  { ...request, confirmation: typed },
                  {
                    onSuccess: (done) => {
                      setResult(done);
                      setPlan(null);
                      toast.push({
                        title: t("migration.done", { count: done.moved }),
                        tone: "success",
                      });
                    },
                  },
                );
              }}
            >
              <Field label={t("migration.confirmLabel", { text: plan.confirmation })}>
                {(c) => (
                  <Input
                    {...c}
                    value={typed}
                    autoComplete="off"
                    onChange={(e) => setTyped(e.target.value)}
                  />
                )}
              </Field>
              <div>
                <Button
                  type="submit"
                  disabled={typed.trim() !== plan.confirmation}
                  loading={execute.isPending}
                >
                  {t("migration.execute")}
                </Button>
              </div>
            </form>
          ) : null}
        </>
      ) : null}
      {result ? (
        <div>
          <p>{t("migration.result", { moved: result.moved, failed: result.failed })}</p>
          <Button
            variant="secondary"
            icon={<Download size={14} />}
            onClick={() => download("relink.csv", result.relink_csv)}
          >
            {t("migration.relink")}
          </Button>
        </div>
      ) : null}
    </Card>
  );
}

/** Optional tool to re-file existing assets; switched off by default. */
export function MigrationAdmin() {
  const { t } = useTranslation();
  const settings = useMigrationSettings();
  const toggle = useSetMigrationEnabled();
  return (
    <>
      <p className="muted">{t("migration.lead")}</p>
      <Switch
        label={t("migration.enable")}
        checked={settings.data?.enabled ?? false}
        disabled={!settings.data || toggle.isPending}
        onCheckedChange={(on) => toggle.mutate(on)}
      />
      {settings.data?.enabled ? <Tool /> : null}
    </>
  );
}
