import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderOpen } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  Badge,
  Button,
  Card,
  Field,
  Input,
  Select,
  Switch,
  useTheme,
  type ThemePreference,
} from "@rushbase/ui";

import { useBridge } from "../bridge/context";
import type { Settings } from "../bridge/types";
import { setLanguage, type Language } from "../i18n";
import { usePlatform, useSettings } from "../state/queries";

/** Server, library mount, worker command, language/theme, autostart and update status. */
export function SettingsPage({ onWizard }: { onWizard: () => void }) {
  const { t, i18n } = useTranslation();
  const bridge = useBridge();
  const client = useQueryClient();
  const { preference, setPreference } = useTheme();
  const { data: stored } = useSettings();
  const platform = usePlatform().data;
  const autostart = useQuery({
    queryKey: ["autostart"],
    queryFn: () => bridge.autostart.get(),
    retry: false,
  });
  const [edited, setDraft] = useState<Settings | null>(null);
  const [saved, setSaved] = useState(false);
  const draft = edited ?? stored;
  if (!draft) return null;

  const patch = (change: Partial<Settings>) => {
    setSaved(false);
    setDraft({ ...draft, ...change });
  };
  const save = async () => {
    const clean = (v: string | null) => (v && v.trim() ? v.trim() : null);
    await bridge.setSettings({
      ...draft,
      server_url: clean(draft.server_url),
      library_root: clean(draft.library_root),
      cli_command: clean(draft.cli_command),
      language: i18n.language,
    });
    await client.invalidateQueries();
    setSaved(true);
  };

  return (
    <div className="dk-page dk-settings">
      <Card title={t("settings.connection")}>
        <div className="dk-form">
          <Field label={t("settings.server")}>
            {(c) => (
              <Input
                {...c}
                value={draft.server_url ?? ""}
                placeholder="http://rushbase.lan:8080"
                onChange={(e) => patch({ server_url: e.target.value })}
              />
            )}
          </Field>
          <Field label={t("settings.libraryRoot")} hint={t("settings.libraryRootHint")}>
            {(c) => (
              <div className="dk-inline">
                <Input
                  {...c}
                  className="dk-mono"
                  value={draft.library_root ?? ""}
                  onChange={(e) => patch({ library_root: e.target.value })}
                />
                <Button
                  variant="secondary"
                  icon={<FolderOpen size={16} />}
                  onClick={() =>
                    void bridge.pickDirectory().then((d) => d && patch({ library_root: d }))
                  }
                >
                  {t("common.browse")}
                </Button>
              </div>
            )}
          </Field>
          <Field label={t("settings.cli")} hint={t("settings.cliHint")}>
            {(c) => (
              <Input
                {...c}
                className="dk-mono"
                value={draft.cli_command ?? ""}
                placeholder="rushbase"
                onChange={(e) => patch({ cli_command: e.target.value })}
              />
            )}
          </Field>
          <div className="dk-actions">
            <Button onClick={() => void save()}>{t("common.save")}</Button>
            {saved ? (
              <span className="dk-ok" role="status">
                {t("settings.saved")}
              </span>
            ) : null}
            <Button variant="ghost" onClick={onWizard}>
              {t("settings.wizard")}
            </Button>
          </div>
        </div>
      </Card>

      <Card title={t("settings.appearance")}>
        <div className="dk-form">
          <Field label={t("settings.language")}>
            {(c) => (
              <Select
                id={c.id}
                value={i18n.language}
                onValueChange={(value) => void setLanguage(value as Language)}
                options={[
                  { value: "de", label: "Deutsch" },
                  { value: "en", label: "English" },
                ]}
              />
            )}
          </Field>
          <Field label={t("settings.theme")}>
            {(c) => (
              <Select
                id={c.id}
                value={preference}
                onValueChange={(value) => setPreference(value as ThemePreference)}
                options={[
                  { value: "system", label: t("settings.themeSystem") },
                  { value: "light", label: t("settings.themeLight") },
                  { value: "dark", label: t("settings.themeDark") },
                ]}
              />
            )}
          </Field>
        </div>
      </Card>

      <Card title={t("settings.system")}>
        <div className="dk-form">
          <Switch
            checked={autostart.data ?? false}
            disabled={autostart.isError}
            label={t("settings.autostart")}
            onCheckedChange={(on) =>
              void bridge.autostart
                .set(on)
                .then(() => client.invalidateQueries({ queryKey: ["autostart"] }))
            }
          />
          <div className="dk-badges">
            <Badge tone={platform?.tray ? "success" : "warning"}>
              {platform?.tray ? t("settings.trayOn") : t("settings.trayOff")}
            </Badge>
            <Badge tone={platform?.updater ? "success" : "neutral"}>
              {platform?.updater ? t("settings.updaterOn") : t("settings.updaterOff")}
            </Badge>
            {platform?.wayland ? <Badge tone="info">Wayland</Badge> : null}
          </div>
          <p className="dk-muted">
            {platform?.updater ? t("settings.updaterHintOn") : t("settings.updaterHintOff")}
          </p>
          <div className="dk-actions">
            <Button variant="danger" onClick={() => void bridge.quit()}>
              {t("settings.quit")}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
