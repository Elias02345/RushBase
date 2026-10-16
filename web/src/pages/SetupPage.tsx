import { Button, Field, Input, Select, useToast } from "@rushbase/ui";
import { useNavigate } from "@tanstack/react-router";
import {
  ArrowLeft,
  ArchiveRestore,
  Check,
  FolderOpen,
  FolderPlus,
  Copy,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import {
  useCreateEnrollment,
  useRestore,
  useSaveLibrarySettings,
  useSetup,
  useSetupStatus,
} from "../api/queries";
import { setLanguage, type Language } from "../i18n";
import { copyText, enrollCommand } from "../lib/platform";
import { Logo } from "../shell/Logo";

const MIN_PASSWORD = 10;
type LibraryMode = "new" | "adopt" | "restore";
const STEPS = ["language", "admin", "library", "paths", "done"] as const;

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError && error.message ? error.message : fallback;
}

function ModeCard({
  icon: Icon,
  title,
  text,
  selected,
  onSelect,
}: {
  icon: LucideIcon;
  title: string;
  text: string;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      className={`mode-card${selected ? " mode-card--selected" : ""}`}
      onClick={onSelect}
    >
      <Icon size={22} aria-hidden="true" />
      <span className="mode-card__title">{title}</span>
      <span className="mode-card__text">{text}</span>
    </button>
  );
}

export function SetupPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const toast = useToast();
  const setupStatus = useSetupStatus();
  const setup = useSetup();
  const restore = useRestore();
  const savePaths = useSaveLibrarySettings();
  const enrollment = useCreateEnrollment();

  const [step, setStep] = useState(0);
  const [admin, setAdmin] = useState({ displayName: "", username: "", password: "", confirm: "" });
  const [adminError, setAdminError] = useState<string | null>(null);
  const [mode, setMode] = useState<LibraryMode | null>(null);
  const [libraryName, setLibraryName] = useState("Rushbase");
  const [backupName, setBackupName] = useState("");
  const [paths, setPaths] = useState({ windows: "", macos: "", linux: "" });
  const language: Language = i18n.language === "en" ? "en" : "de";

  const status = setupStatus.data;
  const effectiveMode: LibraryMode = mode ?? (status?.library_initialised ? "adopt" : "new");
  const enrolled = useRef(false);

  // Create the first worker token once, when the final step appears.
  useEffect(() => {
    if (step === 4 && !enrolled.current) {
      enrolled.current = true;
      enrollment.mutate(t("setup.firstWorkerNote"));
    }
  }, [step, enrollment, t]);

  useEffect(() => {
    if (status && !status.needs_setup && step === 0) void navigate({ to: "/login" });
  }, [status, step, navigate]);

  const submitAdmin = (event: FormEvent) => {
    event.preventDefault();
    if (admin.password.length < MIN_PASSWORD) {
      setAdminError(t("setup.passwordShort", { count: MIN_PASSWORD }));
    } else if (admin.password !== admin.confirm) {
      setAdminError(t("setup.passwordMismatch"));
    } else {
      setAdminError(null);
      setStep(2);
    }
  };

  const submitLibrary = (event: FormEvent) => {
    event.preventDefault();
    if (effectiveMode === "restore") {
      const name = backupName || status?.backups[0]?.name;
      if (!name) return;
      restore.mutate(name, {
        onSuccess: () => {
          toast.push({
            title: t("setup.restored"),
            description: t("setup.restoredText"),
            tone: "success",
          });
          void navigate({ to: "/login" });
        },
      });
      return;
    }
    setup.mutate(
      {
        username: admin.username.trim(),
        password: admin.password,
        display_name: admin.displayName.trim(),
        library_name: libraryName.trim() || "Rushbase",
        locale: language,
      },
      { onSuccess: () => setStep(3) },
    );
  };

  const submitPaths = (event: FormEvent) => {
    event.preventDefault();
    const roots = {
      windows: paths.windows.trim() || null,
      macos: paths.macos.trim() || null,
      linux: paths.linux.trim() || null,
    };
    if (!roots.windows && !roots.macos && !roots.linux) return setStep(4);
    savePaths.mutate({ path_roots: roots, central_meta: false }, { onSuccess: () => setStep(4) });
  };

  const token = enrollment.data?.token;
  const command = token ? enrollCommand(token) : "";
  const copy = async (text: string) => {
    const ok = await copyText(text);
    toast.push({
      title: ok ? t("common.copied") : t("common.copyFailed"),
      tone: ok ? "success" : "error",
    });
  };

  return (
    <div className="auth-page">
      <div className="auth-art" aria-hidden="true">
        <span className="auth-art__tile auth-art__tile--a" />
        <span className="auth-art__tile auth-art__tile--b" />
        <span className="auth-art__tile auth-art__tile--c" />
      </div>
      <div className="wizard">
        <div className="auth-card__brand">
          <Logo size={44} />
          <span>Rushbase</span>
        </div>
        <ol className="stepper" aria-label={t("setup.progress")}>
          {STEPS.map((key, index) => (
            <li
              key={key}
              className="stepper__item"
              aria-current={index === step ? "step" : undefined}
              data-state={index < step ? "done" : index === step ? "current" : "todo"}
            >
              <span className="stepper__dot">
                {index < step ? <Check size={14} strokeWidth={3} aria-hidden="true" /> : index + 1}
              </span>
              <span className="stepper__label">{t(`setup.steps.${key}`)}</span>
            </li>
          ))}
        </ol>

        <div className="wizard__panel" key={step}>
          {step === 0 && (
            <section aria-labelledby="step-title">
              <h1 id="step-title" className="wizard__title">
                {t("setup.language.title")}
              </h1>
              <p className="wizard__lead">{t("setup.language.lead")}</p>
              <div className="choice-row" role="radiogroup" aria-labelledby="step-title">
                {(["de", "en"] as const).map((code) => (
                  <button
                    key={code}
                    type="button"
                    role="radio"
                    aria-checked={language === code}
                    className={`mode-card${language === code ? " mode-card--selected" : ""}`}
                    onClick={() => void setLanguage(code)}
                  >
                    <span className="mode-card__title">
                      {code === "de" ? "Deutsch" : "English"}
                    </span>
                    <span className="mode-card__text">
                      {code === "de" ? t("setup.language.deText") : t("setup.language.enText")}
                    </span>
                  </button>
                ))}
              </div>
              <div className="wizard__actions">
                <Button size="lg" onClick={() => setStep(1)}>
                  {t("common.next")}
                </Button>
              </div>
            </section>
          )}

          {step === 1 && (
            <form onSubmit={submitAdmin} aria-labelledby="step-title">
              <h1 id="step-title" className="wizard__title">
                {t("setup.admin.title")}
              </h1>
              <p className="wizard__lead">{t("setup.admin.lead")}</p>
              <div className="form-grid">
                <Field label={t("setup.admin.displayName")}>
                  {(control) => (
                    <Input
                      {...control}
                      autoComplete="name"
                      value={admin.displayName}
                      onChange={(e) => setAdmin({ ...admin, displayName: e.target.value })}
                    />
                  )}
                </Field>
                <Field label={t("setup.admin.username")}>
                  {(control) => (
                    <Input
                      {...control}
                      autoComplete="username"
                      required
                      value={admin.username}
                      onChange={(e) => setAdmin({ ...admin, username: e.target.value })}
                    />
                  )}
                </Field>
                <Field
                  label={t("setup.admin.password")}
                  hint={t("setup.admin.passwordHint", { count: MIN_PASSWORD })}
                >
                  {(control) => (
                    <Input
                      {...control}
                      type="password"
                      autoComplete="new-password"
                      required
                      value={admin.password}
                      onChange={(e) => setAdmin({ ...admin, password: e.target.value })}
                    />
                  )}
                </Field>
                <Field label={t("setup.admin.confirm")} error={adminError ?? undefined}>
                  {({ invalid, ...control }) => (
                    <Input
                      {...control}
                      invalid={invalid}
                      type="password"
                      autoComplete="new-password"
                      required
                      value={admin.confirm}
                      onChange={(e) => setAdmin({ ...admin, confirm: e.target.value })}
                    />
                  )}
                </Field>
              </div>
              <div className="wizard__actions">
                <Button variant="ghost" icon={<ArrowLeft size={16} />} onClick={() => setStep(0)}>
                  {t("common.back")}
                </Button>
                <Button type="submit" size="lg">
                  {t("common.next")}
                </Button>
              </div>
            </form>
          )}

          {step === 2 && (
            <form onSubmit={submitLibrary} aria-labelledby="step-title">
              <h1 id="step-title" className="wizard__title">
                {t("setup.library.title")}
              </h1>
              <p className="wizard__lead">{t("setup.library.lead")}</p>
              <div className="choice-stack" role="radiogroup" aria-labelledby="step-title">
                <ModeCard
                  icon={FolderPlus}
                  title={t("setup.library.new")}
                  text={t("setup.library.newText")}
                  selected={effectiveMode === "new"}
                  onSelect={() => setMode("new")}
                />
                <ModeCard
                  icon={FolderOpen}
                  title={t("setup.library.adopt")}
                  text={
                    status?.library_initialised
                      ? t("setup.library.adoptFound")
                      : t("setup.library.adoptText")
                  }
                  selected={effectiveMode === "adopt"}
                  onSelect={() => setMode("adopt")}
                />
                {status?.restore_available ? (
                  <ModeCard
                    icon={ArchiveRestore}
                    title={t("setup.library.restore")}
                    text={t("setup.library.restoreText", { count: status.backups.length })}
                    selected={effectiveMode === "restore"}
                    onSelect={() => setMode("restore")}
                  />
                ) : null}
              </div>
              {effectiveMode === "new" ? (
                <Field label={t("setup.library.name")} className="wizard__field">
                  {(control) => (
                    <Input
                      {...control}
                      value={libraryName}
                      onChange={(e) => setLibraryName(e.target.value)}
                    />
                  )}
                </Field>
              ) : null}
              {effectiveMode === "restore" && status ? (
                <Field label={t("setup.library.backup")} className="wizard__field">
                  {(control) => (
                    <Select
                      id={control.id}
                      value={backupName || status.backups[0]?.name || ""}
                      onValueChange={setBackupName}
                      options={status.backups.map((b) => ({ value: b.name, label: b.name }))}
                    />
                  )}
                </Field>
              ) : null}
              {setup.error || restore.error ? (
                <p className="form-error" role="alert">
                  {errorText(setup.error ?? restore.error, t("setup.library.failed"))}
                </p>
              ) : null}
              <div className="wizard__actions">
                <Button variant="ghost" icon={<ArrowLeft size={16} />} onClick={() => setStep(1)}>
                  {t("common.back")}
                </Button>
                <Button type="submit" size="lg" loading={setup.isPending || restore.isPending}>
                  {effectiveMode === "restore"
                    ? t("setup.library.doRestore")
                    : t("setup.library.create")}
                </Button>
              </div>
            </form>
          )}

          {step === 3 && (
            <form onSubmit={submitPaths} aria-labelledby="step-title">
              <h1 id="step-title" className="wizard__title">
                {t("setup.paths.title")}
              </h1>
              <p className="wizard__lead">{t("setup.paths.lead")}</p>
              <div className="form-grid">
                {(["windows", "macos", "linux"] as const).map((os) => (
                  <Field key={os} label={t(`os.${os}`)} hint={t(`setup.paths.hint.${os}`)}>
                    {(control) => (
                      <Input
                        {...control}
                        className="rb-input--mono"
                        spellCheck={false}
                        placeholder={t(`setup.paths.example.${os}`)}
                        value={paths[os]}
                        onChange={(e) => setPaths({ ...paths, [os]: e.target.value })}
                      />
                    )}
                  </Field>
                ))}
              </div>
              {savePaths.error ? (
                <p className="form-error" role="alert">
                  {errorText(savePaths.error, t("setup.paths.failed"))}
                </p>
              ) : null}
              <div className="wizard__actions">
                <Button variant="ghost" onClick={() => setStep(4)}>
                  {t("setup.paths.skip")}
                </Button>
                <Button type="submit" size="lg" loading={savePaths.isPending}>
                  {t("common.next")}
                </Button>
              </div>
            </form>
          )}

          {step === 4 && (
            <section aria-labelledby="step-title">
              <h1 id="step-title" className="wizard__title">
                {t("setup.done.title")}
              </h1>
              <p className="wizard__lead">{t("setup.done.lead")}</p>
              <div className="token-box">
                <h2 className="token-box__title">{t("setup.done.tokenTitle")}</h2>
                {enrollment.isError ? (
                  <p className="form-error" role="alert">
                    {errorText(enrollment.error, t("setup.done.tokenFailed"))}
                  </p>
                ) : token ? (
                  <>
                    <code className="codeblock" data-testid="enroll-command">
                      {command}
                    </code>
                    <div className="token-box__row">
                      <Button
                        variant="secondary"
                        size="sm"
                        icon={<Copy size={14} />}
                        onClick={() => void copy(command)}
                      >
                        {t("setup.done.copyCommand")}
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => void copy(token)}>
                        {t("setup.done.copyToken")}
                      </Button>
                    </div>
                    <p className="token-box__note">{t("setup.done.tokenNote")}</p>
                  </>
                ) : (
                  <p className="token-box__note">{t("common.loading")}</p>
                )}
              </div>
              <div className="wizard__actions">
                <Button size="lg" onClick={() => void navigate({ to: "/" })}>
                  {t("setup.done.open")}
                </Button>
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
