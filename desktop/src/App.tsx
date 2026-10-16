import { Library, Settings as Cog, Zap } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { cx } from "@rushbase/ui";

import { LibraryPage } from "./pages/LibraryPage";
import { ProcessorPage } from "./pages/ProcessorPage";
import { SettingsPage } from "./pages/SettingsPage";
import { WizardPage } from "./pages/WizardPage";
import { useOverview, useSettings } from "./state/queries";
import { useRun } from "./state/run";

type View = "processor" | "library" | "settings" | "wizard";
const NAV: { view: Exclude<View, "wizard">; icon: typeof Zap; key: string }[] = [
  { view: "processor", icon: Zap, key: "nav.processor" },
  { view: "library", icon: Library, key: "nav.library" },
  { view: "settings", icon: Cog, key: "nav.settings" },
];

function initialView(): View {
  const hash = window.location.hash.replace("#", "");
  return hash === "library" || hash === "settings" || hash === "wizard" ? hash : "processor";
}

/** Window shell: Processor (default), Library, Settings and the first-run wizard. */
export function App() {
  const { t } = useTranslation();
  const [view, setView] = useState<View>(initialView);
  const settings = useSettings();
  const overview = useOverview();
  const { state } = useRun();
  const [guided, setGuided] = useState(true);

  // First run: nothing configured yet, so guide the user through the wizard once.
  const needsSetup =
    !!settings.data && !settings.data.server_url && !!overview.data && !overview.data.enrolled;
  const shown: View = needsSetup && guided && view === "processor" ? "wizard" : view;
  const go = (next: View) => {
    setGuided(false);
    setView(next);
  };

  const running = state.phase === "running" || state.phase === "starting";
  return (
    <div className="dk-shell">
      <nav className="dk-nav" aria-label={t("nav.label")}>
        <span className="dk-brand">Rushbase</span>
        {NAV.map(({ view: v, icon: Icon, key }) => (
          <button
            key={v}
            type="button"
            className={cx("dk-nav__item", shown === v && "is-active")}
            aria-current={shown === v ? "page" : undefined}
            onClick={() => go(v)}
          >
            <Icon size={18} aria-hidden="true" />
            {t(key)}
            {v === "processor" && running ? (
              <span className="dk-pulse" aria-label={t("run.running")} />
            ) : null}
          </button>
        ))}
      </nav>
      <main className="dk-main">
        {shown === "processor" ? (
          <ProcessorPage onSetup={() => go("wizard")} onSettings={() => go("settings")} />
        ) : null}
        {shown === "library" ? <LibraryPage onSetup={() => go("wizard")} /> : null}
        {shown === "settings" ? <SettingsPage onWizard={() => go("wizard")} /> : null}
        {shown === "wizard" ? <WizardPage onDone={() => go("processor")} /> : null}
      </main>
    </div>
  );
}
