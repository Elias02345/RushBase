import {
  Activity,
  Command as CommandIcon,
  Keyboard,
  Languages,
  LayoutDashboard,
  Library,
  LogOut,
  Monitor,
  Moon,
  Pause,
  Play,
  Settings2,
  Sun,
  type LucideIcon,
} from "lucide-react";
import {
  CommandPalette,
  Dialog,
  IconButton,
  Kbd,
  SearchField,
  useTheme,
  type CommandGroup,
} from "@rushbase/ui";
import { Link, Outlet, useLocation, useNavigate } from "@tanstack/react-router";
import { useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { hasRole, useLogout, useMe, useQueuePaused, useStatus } from "../api/queries";
import { setLanguage, type Language } from "../i18n";
import { isApple } from "../lib/platform";
import { Logo } from "./Logo";
import { useGlobalShortcuts } from "./shortcuts";

interface NavItem {
  to: "/" | "/library" | "/processing" | "/admin";
  icon: LucideIcon;
  label: string;
  admin?: boolean;
}

const NAV: NavItem[] = [
  { to: "/", icon: LayoutDashboard, label: "nav.dashboard" },
  { to: "/library", icon: Library, label: "nav.library" },
  { to: "/processing", icon: Activity, label: "nav.processing" },
  { to: "/admin", icon: Settings2, label: "nav.admin", admin: true },
];

const THEME_CYCLE = { system: "light", light: "dark", dark: "system" } as const;

function ShortcutRow({ keys, label }: { keys: string[]; label: string }) {
  return (
    <li className="shortcut-row">
      <span>{label}</span>
      <span className="shortcut-row__keys">
        {keys.map((key) => (
          <Kbd key={key}>{key}</Kbd>
        ))}
      </span>
    </li>
  );
}

/** Authenticated frame: navigation, global search, shortcuts and the command palette. */
export function AppShell() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { preference, setPreference } = useTheme();
  const me = useMe().data;
  const status = useStatus().data;
  const logout = useLogout();
  const queue = useQueuePaused();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [topSearch, setTopSearch] = useState("");

  const isAdmin = hasRole(me?.role, "admin");
  const mod = isApple() ? "⌘" : "Ctrl";
  const language: Language = i18n.language === "en" ? "en" : "de";
  const nextLanguage: Language = language === "de" ? "en" : "de";
  const pending = status
    ? Object.values(status.stages).reduce((sum, stage) => sum + stage.pending_jobs, 0)
    : 0;

  const go = useCallback(
    (target: "dashboard" | "library" | "processing" | "admin") => {
      const to = {
        dashboard: "/",
        library: "/library",
        processing: "/processing",
        admin: "/admin",
      } as const;
      if (target === "admin" && !isAdmin) return;
      void navigate({ to: to[target] });
    },
    [navigate, isAdmin],
  );

  const focusSearch = useCallback(() => {
    const field = document.querySelector<HTMLInputElement>("[data-search-input]");
    if (field) {
      field.focus();
      field.select();
    } else {
      void navigate({ to: "/library" });
    }
  }, [navigate]);

  useGlobalShortcuts({
    togglePalette: useCallback(() => setPaletteOpen((open) => !open), []),
    openHelp: useCallback(() => setHelpOpen(true), []),
    go,
    focusSearch,
  });

  const onLibrary = location.pathname.startsWith("/library");

  const groups = useMemo<CommandGroup[]>(() => {
    const navItems = NAV.filter((item) => !item.admin || isAdmin).map((item) => ({
      id: item.to,
      label: t(item.label),
      icon: <item.icon size={18} />,
      keywords: [t("palette.goTo")],
      onSelect: () => void navigate({ to: item.to }),
    }));
    const actions = [
      {
        id: "theme",
        label: t("palette.toggleTheme"),
        icon: <Sun size={18} />,
        onSelect: () => setPreference(THEME_CYCLE[preference]),
      },
      {
        id: "language",
        label: t("palette.switchLanguage", { language: nextLanguage.toUpperCase() }),
        icon: <Languages size={18} />,
        onSelect: () => void setLanguage(nextLanguage),
      },
      {
        id: "help",
        label: t("palette.shortcuts"),
        icon: <Keyboard size={18} />,
        shortcut: "?",
        onSelect: () => setHelpOpen(true),
      },
      ...(isAdmin
        ? [
            {
              id: "queue",
              label: status?.queue_paused ? t("palette.resumeQueue") : t("palette.pauseQueue"),
              icon: status?.queue_paused ? <Play size={18} /> : <Pause size={18} />,
              onSelect: () => queue.mutate(!status?.queue_paused),
            },
          ]
        : []),
      {
        id: "logout",
        label: t("nav.logout"),
        icon: <LogOut size={18} />,
        onSelect: () =>
          logout.mutate(undefined, { onSuccess: () => void navigate({ to: "/login" }) }),
      },
    ];
    return [
      { heading: t("palette.goTo"), items: navItems },
      { heading: t("palette.actions"), items: actions },
    ];
  }, [t, isAdmin, navigate, setPreference, preference, nextLanguage, status, queue, logout]);

  const ThemeIcon = preference === "system" ? Monitor : preference === "dark" ? Moon : Sun;

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        {t("nav.skip")}
      </a>
      <aside className="sidebar">
        <Link to="/" className="sidebar__brand" aria-label={t("nav.home")}>
          <Logo />
          <span className="sidebar__brand-name">Rushbase</span>
        </Link>
        <nav aria-label={t("nav.main")} className="sidebar__nav">
          {NAV.filter((item) => !item.admin || isAdmin).map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className="navlink"
              activeOptions={{ exact: item.to === "/" }}
            >
              <item.icon size={20} aria-hidden="true" />
              <span className="navlink__label">{t(item.label)}</span>
              {item.to === "/processing" && pending > 0 ? (
                <span className="navlink__count" aria-label={t("nav.pending", { count: pending })}>
                  {pending > 99 ? "99+" : pending}
                </span>
              ) : null}
            </Link>
          ))}
        </nav>
        <div className="sidebar__foot">
          <div className="usercard">
            <span className="usercard__avatar" aria-hidden="true">
              {(me?.display_name ?? "?").slice(0, 1).toUpperCase()}
            </span>
            <span className="usercard__text">
              <span className="usercard__name">{me?.display_name}</span>
              <span className="usercard__role">{me ? t(`roles.${me.role}`) : ""}</span>
            </span>
            <IconButton
              label={t("nav.logout")}
              icon={<LogOut size={18} />}
              size="sm"
              onClick={() =>
                logout.mutate(undefined, { onSuccess: () => void navigate({ to: "/login" }) })
              }
            />
          </div>
        </div>
      </aside>

      <div className="shell__body">
        <header className="topbar">
          {onLibrary ? (
            <div className="topbar__spacer" />
          ) : (
            <form
              className="topbar__search"
              role="search"
              onSubmit={(event) => {
                event.preventDefault();
                void navigate({ to: "/library", search: { q: topSearch.trim() || undefined } });
                setTopSearch("");
              }}
            >
              <SearchField
                data-search-input=""
                value={topSearch}
                onValueChange={setTopSearch}
                clearLabel={t("common.clear")}
                aria-label={t("library.searchLabel")}
                placeholder={t("library.searchPlaceholderShort")}
                shortcut="/"
              />
            </form>
          )}
          <div className="topbar__actions">
            <button type="button" className="palette-trigger" onClick={() => setPaletteOpen(true)}>
              <CommandIcon size={16} aria-hidden="true" />
              <span>{t("palette.open")}</span>
              <Kbd aria-hidden="true">{mod} K</Kbd>
            </button>
            <IconButton
              label={t("nav.language", { language: nextLanguage.toUpperCase() })}
              icon={<span className="lang-badge">{language.toUpperCase()}</span>}
              onClick={() => void setLanguage(nextLanguage)}
            />
            <IconButton
              label={t(`theme.${preference}`)}
              icon={<ThemeIcon size={19} />}
              onClick={() => setPreference(THEME_CYCLE[preference])}
            />
            <IconButton
              label={t("nav.shortcuts")}
              icon={<Keyboard size={19} />}
              onClick={() => setHelpOpen(true)}
            />
          </div>
        </header>
        <main id="main" className="main" tabIndex={-1}>
          <Outlet />
        </main>
      </div>

      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        groups={groups}
        label={t("palette.label")}
        placeholder={t("palette.placeholder")}
        emptyLabel={t("palette.empty")}
      />
      <Dialog
        open={helpOpen}
        onOpenChange={setHelpOpen}
        title={t("shortcuts.title")}
        description={t("shortcuts.description")}
        closeLabel={t("common.close")}
      >
        <ul className="shortcut-list">
          <ShortcutRow keys={[mod, "K"]} label={t("shortcuts.palette")} />
          <ShortcutRow keys={["/"]} label={t("shortcuts.search")} />
          <ShortcutRow keys={["G", "D"]} label={t("shortcuts.goDashboard")} />
          <ShortcutRow keys={["G", "L"]} label={t("shortcuts.goLibrary")} />
          <ShortcutRow keys={["G", "P"]} label={t("shortcuts.goProcessing")} />
          <ShortcutRow keys={["← ↑ → ↓"]} label={t("shortcuts.grid")} />
          <ShortcutRow keys={["Enter"]} label={t("shortcuts.open")} />
          <ShortcutRow keys={["Space"]} label={t("shortcuts.select")} />
          <ShortcutRow keys={["Esc"]} label={t("shortcuts.escape")} />
          <ShortcutRow keys={["?"]} label={t("shortcuts.help")} />
        </ul>
      </Dialog>
    </div>
  );
}
