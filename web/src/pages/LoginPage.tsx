import { Button, Field, Input } from "@rushbase/ui";
import { useRouter, useSearch } from "@tanstack/react-router";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { useLogin } from "../api/queries";
import { IS_MOCK } from "../lib/mock";
import { Logo } from "../shell/Logo";

export function LoginPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const { redirect } = useSearch({ from: "/login" });
  const login = useLogin();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate(
      { username, password },
      { onSuccess: () => router.history.push(redirect ?? "/") },
    );
  };

  const error = login.error;
  const message =
    error instanceof ApiError
      ? error.status === 429
        ? t("login.tooMany", { seconds: error.retryAfter ?? 60 })
        : error.status === 401
          ? t("login.invalid")
          : t("login.failed")
      : error
        ? t("login.unreachable")
        : undefined;

  return (
    <div className="auth-page">
      <div className="auth-art" aria-hidden="true">
        <span className="auth-art__tile auth-art__tile--a" />
        <span className="auth-art__tile auth-art__tile--b" />
        <span className="auth-art__tile auth-art__tile--c" />
      </div>
      <form className="auth-card" onSubmit={submit} aria-labelledby="login-title">
        <div className="auth-card__brand">
          <Logo size={44} />
          <span>Rushbase</span>
        </div>
        <h1 id="login-title" className="auth-card__title">
          {t("login.title")}
        </h1>
        <p className="auth-card__lead">{t("login.lead")}</p>
        <Field label={t("login.username")}>
          {({ invalid: _invalid, ...control }) => (
            <Input
              {...control}
              name="username"
              autoComplete="username"
              autoFocus
              required
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          )}
        </Field>
        <Field label={t("login.password")} error={message}>
          {({ invalid, ...control }) => (
            <Input
              {...control}
              invalid={invalid}
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          )}
        </Field>
        <Button type="submit" size="lg" loading={login.isPending}>
          {t("login.submit")}
        </Button>
        {IS_MOCK ? <p className="auth-card__hint">{t("login.demoHint")}</p> : null}
      </form>
    </div>
  );
}
