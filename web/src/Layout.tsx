import { Button, useTheme } from "@rushbase/ui";
import { Outlet } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { setLanguage, type Language } from "./i18n";

export function Layout() {
  const { t, i18n } = useTranslation();
  const { theme, toggleTheme } = useTheme();
  const nextLanguage: Language = i18n.language === "de" ? "en" : "de";

  return (
    <div className="shell">
      <header className="shell__header">
        <span className="shell__brand">
          <span className="shell__logo" aria-hidden="true" />
          Rushbase
        </span>
        <div className="shell__actions">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void setLanguage(nextLanguage)}
            aria-label={t("nav.language")}
          >
            {nextLanguage.toUpperCase()}
          </Button>
          <Button variant="ghost" size="sm" onClick={toggleTheme} aria-label={t("nav.theme")}>
            {theme === "dark" ? "☀️" : "🌙"}
          </Button>
        </div>
      </header>
      <main className="shell__main">
        <Outlet />
      </main>
    </div>
  );
}
