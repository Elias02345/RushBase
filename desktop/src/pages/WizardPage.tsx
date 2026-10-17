import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FolderOpen } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { Button, Card, Field, Input, StatusChip } from "@rushbase/ui";

import { cliData } from "../bridge";
import { useBridge } from "../bridge/context";
import type { Overview, Settings } from "../bridge/types";
import { HardwareCard } from "../components/HardwareCard";
import { ModelsDialog } from "../components/ModelsDialog";
import { parseEnrollCommand } from "../lib/format";
import { useOverview, useSettings } from "../state/queries";

type Step = "server" | "account" | "library" | "hardware" | "check";
const STEPS: Step[] = ["server", "account", "library", "hardware", "check"];

interface Check {
  name: string;
  status: "ok" | "warn" | "error" | "skipped";
  detail: string;
}

/** First-run wizard: server and enrollment, sign-in, library mount, hardware and models, system check. */
export function WizardPage({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const bridge = useBridge();
  const client = useQueryClient();
  const settings = useSettings().data;
  const overview = useOverview().data;
  const [step, setStep] = useState<Step>("server");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [server, setServer] = useState("");
  const [token, setToken] = useState("");
  const [name, setName] = useState(t("wizard.defaultName"));
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [checks, setChecks] = useState<Check[]>([]);
  const [modelsOpen, setModelsOpen] = useState(false);

  const save = async (patch: Partial<Settings>) => {
    const current = settings ?? {
      server_url: null,
      library_root: null,
      cli_command: null,
      language: null,
    };
    await bridge.setSettings({ ...current, ...patch });
    await client.invalidateQueries({ queryKey: ["settings"] });
  };
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };
  const refresh = () => client.invalidateQueries({ queryKey: ["overview"] });

  const connect = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      const parsed = parseEnrollCommand(token);
      const url = (parsed.server ?? server).trim();
      const secret = (parsed.token ?? "").trim();
      if (!url || !secret) throw new Error(t("wizard.serverMissing"));
      await save({ server_url: url });
      await cliData(bridge, [
        "enroll",
        "--server",
        url,
        "--token",
        secret,
        "--name",
        (parsed.name ?? name).trim() || "pc",
      ]);
      await refresh();
      setStep("account");
    });
  };
  const signIn = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      const url = settings?.server_url ?? server;
      await cliData(
        bridge,
        ["login", "--server", url, "--username", username.trim(), "--password-stdin"],
        `${password}\n`,
      );
      setPassword("");
      setStep("library");
    });
  };
  const verifyLibrary = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      const fresh = await cliData<Overview>(bridge, ["overview"]);
      await refresh();
      if (fresh.mount && !fresh.mount.ok)
        throw new Error(fresh.mount.error ?? t("wizard.mountFailed"));
      setStep("hardware");
    });
  };
  const systemCheck = () =>
    void run(async () => {
      // `doctor` exits 1 when a check fails but still prints the list, so read the envelope.
      const result = await bridge.cli<{ checks: Check[] }>(["doctor", "--json"]);
      const data = result.envelope?.data;
      if (!data || !("checks" in data)) throw new Error(result.stderr || t("wizard.check.failed"));
      setChecks(data.checks);
    });

  return (
    <div className="dk-page dk-wizard">
      <ol className="dk-steps" aria-label={t("wizard.steps")}>
        {STEPS.map((s, index) => (
          <li
            key={s}
            aria-current={s === step ? "step" : undefined}
            data-done={STEPS.indexOf(step) > index || undefined}
          >
            <span>{index + 1}</span>
            {t(`wizard.step.${s}`)}
          </li>
        ))}
      </ol>
      {error ? (
        <p role="alert" className="dk-error">
          {error}
        </p>
      ) : null}

      {step === "server" ? (
        <Card title={t("wizard.server.title")}>
          <form className="dk-form" onSubmit={connect}>
            <p className="dk-muted">{t("wizard.server.help")}</p>
            <Field label={t("wizard.server.url")}>
              {(c) => (
                <Input
                  {...c}
                  value={server}
                  placeholder="http://rushbase.lan:8080"
                  onChange={(e) => setServer(e.target.value)}
                />
              )}
            </Field>
            <Field label={t("wizard.server.token")} hint={t("wizard.server.tokenHint")}>
              {(c) => (
                <Input
                  {...c}
                  value={token}
                  className="dk-mono"
                  onChange={(e) => setToken(e.target.value)}
                />
              )}
            </Field>
            <Field label={t("wizard.server.name")}>
              {(c) => <Input {...c} value={name} onChange={(e) => setName(e.target.value)} />}
            </Field>
            <div className="dk-actions">
              <Button type="submit" loading={busy}>
                {t("wizard.server.connect")}
              </Button>
              {overview?.enrolled ? (
                <Button variant="ghost" onClick={() => setStep("account")}>
                  {t("wizard.alreadyEnrolled")}
                </Button>
              ) : null}
            </div>
          </form>
        </Card>
      ) : null}

      {step === "account" ? (
        <Card title={t("wizard.account.title")}>
          <form className="dk-form" onSubmit={signIn}>
            <p className="dk-muted">{t("wizard.account.help")}</p>
            <Field label={t("wizard.account.username")}>
              {(c) => (
                <Input
                  {...c}
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              )}
            </Field>
            <Field label={t("wizard.account.password")}>
              {(c) => (
                <Input
                  {...c}
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              )}
            </Field>
            <div className="dk-actions">
              <Button type="submit" loading={busy} disabled={!username || !password}>
                {t("wizard.account.signIn")}
              </Button>
              <Button variant="ghost" onClick={() => setStep("library")}>
                {t("wizard.skip")}
              </Button>
            </div>
          </form>
        </Card>
      ) : null}

      {step === "library" ? (
        <Card title={t("wizard.library.title")}>
          <form className="dk-form" onSubmit={verifyLibrary}>
            <p className="dk-muted">{t("wizard.library.help")}</p>
            <Field label={t("wizard.library.root")} hint={t("wizard.library.hint")}>
              {(c) => (
                <div className="dk-inline">
                  <Input
                    {...c}
                    className="dk-mono"
                    value={settings?.library_root ?? ""}
                    placeholder="/mnt/rushbase"
                    readOnly
                  />
                  <Button
                    variant="secondary"
                    icon={<FolderOpen size={16} />}
                    onClick={() =>
                      void bridge
                        .pickLibraryRoot()
                        .then(() => client.invalidateQueries({ queryKey: ["settings"] }))
                    }
                  >
                    {t("common.browse")}
                  </Button>
                </div>
              )}
            </Field>
            <div className="dk-actions">
              <Button type="submit" loading={busy} disabled={!settings?.library_root}>
                {t("wizard.library.verify")}
              </Button>
            </div>
          </form>
        </Card>
      ) : null}

      {step === "hardware" ? (
        <>
          {overview ? <HardwareCard hardware={overview.hardware} models={overview.models} /> : null}
          <Card title={t("wizard.hardware.title")}>
            <p className="dk-muted">
              {overview && overview.models.missing.length > 0
                ? t("wizard.hardware.missing", { count: overview.models.missing.length })
                : t("wizard.hardware.complete")}
            </p>
            <div className="dk-actions">
              {overview && overview.models.missing.length > 0 ? (
                <Button onClick={() => setModelsOpen(true)}>{t("processor.loadModels")}</Button>
              ) : null}
              <Button variant="secondary" onClick={() => setStep("check")}>
                {t("wizard.next")}
              </Button>
            </div>
          </Card>
          <ModelsDialog open={modelsOpen} onOpenChange={setModelsOpen} />
        </>
      ) : null}

      {step === "check" ? (
        <Card title={t("wizard.check.title")}>
          <p className="dk-muted">{t("wizard.check.help")}</p>
          {checks.length > 0 ? (
            <ul className="dk-checks">
              {checks.map((c) => (
                <li key={c.name}>
                  <StatusChip
                    tone={
                      c.status === "ok" ? "filed" : c.status === "error" ? "quarantine" : "waiting"
                    }
                  >
                    {c.name}
                  </StatusChip>
                  <span className="dk-muted">{c.detail}</span>
                </li>
              ))}
            </ul>
          ) : null}
          <div className="dk-actions">
            <Button variant="secondary" loading={busy} onClick={systemCheck}>
              {t("wizard.check.run")}
            </Button>
            <Button icon={<CheckCircle2 size={16} />} onClick={onDone}>
              {t("wizard.finish")}
            </Button>
          </div>
        </Card>
      ) : null}
    </div>
  );
}
