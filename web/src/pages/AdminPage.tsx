import {
  Button,
  Card,
  DataTable,
  Dialog,
  EmptyState,
  Field,
  Input,
  Select,
  StatusChip,
  Switch,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  useToast,
  type DataColumn,
} from "@rushbase/ui";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { Copy, DatabaseBackup, Eye, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import type {
  EnrollmentTokenInfo,
  FilingDryRun,
  FilingRule,
  LibrarySettings,
} from "../api/contracts";
import {
  useActivateFilingRule,
  useBackups,
  useCreateBackup,
  useCreateEnrollment,
  useCreateFilingRule,
  useCreateUser,
  useDryRunFilingRule,
  useEnrollmentTokens,
  useFilingRules,
  useLibrarySettings,
  useMe,
  useRevokeEnrollment,
  useSaveLibrarySettings,
  useUpdateUser,
  useUsers,
} from "../api/queries";
import type { Role, User } from "../api/types";
import { formatBytes, formatDateTime } from "../lib/format";
import { copyText, enrollCommand } from "../lib/platform";
import type { AdminTab } from "../routes/search";
import { SearchAdmin } from "./admin/SearchAdmin";
import { McpAdmin } from "./admin/McpAdmin";
import { AuditAdmin } from "./admin/AuditAdmin";
import { MigrationAdmin } from "./admin/MigrationAdmin";
import { TokensAdmin } from "./admin/TokensAdmin";
import { TrashAdmin } from "./admin/TrashAdmin";

const ROLES: Role[] = ["admin", "editor", "viewer"];

function useCopy() {
  const { t } = useTranslation();
  const toast = useToast();
  return async (text: string) => {
    const ok = await copyText(text);
    toast.push({
      title: ok ? t("common.copied") : t("common.copyFailed"),
      tone: ok ? "success" : "error",
    });
  };
}

function apiMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError && error.message ? error.message : fallback;
}

function UsersPanel() {
  const { t } = useTranslation();
  const users = useUsers();
  const me = useMe().data;
  const create = useCreateUser();
  const update = useUpdateUser();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    username: "",
    display_name: "",
    password: "",
    role: "viewer" as Role,
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate(
      { ...form, locale: "de" },
      {
        onSuccess: () => {
          setOpen(false);
          setForm({ username: "", display_name: "", password: "", role: "viewer" });
        },
      },
    );
  };

  const columns: DataColumn<User>[] = [
    {
      key: "name",
      header: t("admin.users.name"),
      width: "minmax(0, 1.6fr)",
      cell: (u) => (
        <span>
          <strong>{u.display_name}</strong> <span className="muted">@{u.username}</span>
        </span>
      ),
    },
    {
      key: "role",
      header: t("admin.users.role"),
      width: "170px",
      cell: (u) => (
        <Select
          aria-label={t("admin.users.roleOf", { name: u.display_name })}
          value={u.role}
          disabled={u.id === me?.id}
          onValueChange={(role) => update.mutate({ id: u.id, role: role as Role })}
          options={ROLES.map((r) => ({ value: r, label: t(`roles.${r}`) }))}
        />
      ),
    },
    {
      key: "login",
      header: t("admin.users.lastLogin"),
      width: "170px",
      cell: (u) => formatDateTime(u.last_login_at),
    },
    {
      key: "active",
      header: t("admin.users.active"),
      width: "130px",
      align: "end",
      cell: (u) => (
        <Switch
          label={t("admin.users.activeLabel", { name: u.display_name })}
          checked={!u.disabled}
          disabled={u.id === me?.id}
          onCheckedChange={(on) => update.mutate({ id: u.id, disabled: !on })}
          className="switch--bare"
        />
      ),
    },
  ];

  return (
    <>
      <div className="panel-head">
        <p className="muted">{t("admin.users.lead")}</p>
        <Button icon={<Plus size={16} />} onClick={() => setOpen(true)}>
          {t("admin.users.add")}
        </Button>
      </div>
      <Card flush>
        <DataTable
          label={t("admin.users.title")}
          rows={users.data ?? []}
          columns={columns}
          getRowId={(u) => u.id}
          rowHeight={64}
        />
      </Card>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title={t("admin.users.add")}
        closeLabel={t("common.close")}
      >
        <form className="form-grid" onSubmit={submit}>
          <Field label={t("setup.admin.displayName")}>
            {(c) => (
              <Input
                {...c}
                value={form.display_name}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              />
            )}
          </Field>
          <Field label={t("setup.admin.username")}>
            {(c) => (
              <Input
                {...c}
                required
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
              />
            )}
          </Field>
          <Field
            label={t("setup.admin.password")}
            hint={t("setup.admin.passwordHint", { count: 10 })}
            error={create.error ? apiMessage(create.error, t("common.error")) : undefined}
          >
            {({ invalid, ...c }) => (
              <Input
                {...c}
                invalid={invalid}
                type="password"
                autoComplete="new-password"
                required
                minLength={10}
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
              />
            )}
          </Field>
          <Field label={t("admin.users.role")}>
            {({ id }) => (
              <Select
                id={id}
                value={form.role}
                onValueChange={(role) => setForm({ ...form, role: role as Role })}
                options={ROLES.map((r) => ({ value: r, label: t(`roles.${r}`) }))}
              />
            )}
          </Field>
          <Button type="submit" loading={create.isPending}>
            {t("admin.users.create")}
          </Button>
        </form>
      </Dialog>
    </>
  );
}

function TokensPanel() {
  const { t } = useTranslation();
  const tokens = useEnrollmentTokens();
  const create = useCreateEnrollment();
  const revoke = useRevokeEnrollment();
  const copy = useCopy();
  const [note, setNote] = useState("");
  const [created, setCreated] = useState<string | null>(null);

  const columns: DataColumn<EnrollmentTokenInfo>[] = [
    {
      key: "note",
      header: t("admin.tokens.note"),
      width: "minmax(0, 1.4fr)",
      cell: (x) => x.note || "–",
    },
    {
      key: "created",
      header: t("admin.tokens.created"),
      width: "170px",
      cell: (x) => formatDateTime(x.created_at),
    },
    {
      key: "state",
      header: t("library.col.status"),
      width: "150px",
      cell: (x) =>
        x.used_at
          ? t("admin.tokens.used")
          : Date.parse(x.expires_at) <= Date.now()
            ? t("admin.tokens.expired")
            : t("admin.tokens.open"),
    },
    {
      key: "actions",
      header: "",
      width: "120px",
      align: "end",
      cell: (x) => (
        <Button
          size="sm"
          variant="ghost"
          disabled={Boolean(x.used_at) || Date.parse(x.expires_at) <= Date.now()}
          onClick={() => revoke.mutate(x.id)}
        >
          {t("admin.tokens.revoke")}
        </Button>
      ),
    },
  ];
  const command = created ? enrollCommand(created) : "";

  return (
    <>
      <div className="panel-head">
        <p className="muted">{t("admin.tokens.lead")}</p>
        <form
          className="inline-form"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate(note, {
              onSuccess: (result) => {
                setCreated(result.token);
                setNote("");
              },
            });
          }}
        >
          <Input
            aria-label={t("admin.tokens.note")}
            placeholder={t("admin.tokens.notePlaceholder")}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <Button type="submit" icon={<Plus size={16} />} loading={create.isPending}>
            {t("admin.tokens.create")}
          </Button>
        </form>
      </div>
      <Card flush>
        <DataTable
          label={t("admin.tokens.title")}
          rows={tokens.data ?? []}
          columns={columns}
          getRowId={(x) => x.id}
          rowHeight={56}
          empty={
            <EmptyState title={t("admin.tokens.empty")} description={t("admin.tokens.emptyText")} />
          }
        />
      </Card>
      <Dialog
        open={created !== null}
        onOpenChange={(open) => !open && setCreated(null)}
        title={t("admin.tokens.createdTitle")}
        description={t("admin.tokens.createdText")}
        closeLabel={t("common.close")}
        footer={
          <Button icon={<Copy size={16} />} onClick={() => void copy(command)}>
            {t("setup.done.copyCommand")}
          </Button>
        }
      >
        <code className="codeblock">{command}</code>
      </Dialog>
    </>
  );
}

function RulesPanel() {
  const { t } = useTranslation();
  const rules = useFilingRules();
  const activate = useActivateFilingRule();
  const toast = useToast();
  const columns: DataColumn<FilingRule>[] = [
    {
      key: "version",
      header: t("admin.rules.version"),
      width: "90px",
      cell: (r) => `v${r.version}`,
    },
    {
      key: "template",
      header: t("admin.rules.template"),
      width: "minmax(0, 2fr)",
      cell: (r) => <code>{r.template}</code>,
    },
    {
      key: "note",
      header: t("admin.tokens.note"),
      width: "minmax(0, 1fr)",
      cell: (r) => r.note || "–",
    },
    {
      key: "created",
      header: t("admin.tokens.created"),
      width: "170px",
      cell: (r) => formatDateTime(r.created_at),
    },
    {
      key: "actions",
      header: "",
      width: "130px",
      align: "end",
      cell: (r) =>
        r.active ? (
          <StatusChip tone="filed">{t("admin.rules.active")}</StatusChip>
        ) : (
          <Button
            size="sm"
            variant="secondary"
            loading={activate.isPending && activate.variables === r.id}
            onClick={() =>
              activate.mutate(r.id, {
                onSuccess: () => toast.push({ title: t("admin.rules.activated"), tone: "success" }),
              })
            }
          >
            {t("admin.rules.activate")}
          </Button>
        ),
    },
  ];
  return (
    <>
      <p className="muted">{t("admin.rules.lead")}</p>
      <Card flush>
        <DataTable
          label={t("admin.tabs.rules")}
          rows={rules.data ?? []}
          columns={columns}
          getRowId={(r) => r.id}
          rowHeight={56}
          empty={<EmptyState title={t("admin.rules.empty")} description="" />}
        />
      </Card>
      <NewRuleVersion latest={rules.data?.find((r) => r.active)?.template ?? ""} />
    </>
  );
}

function NewRuleVersion({ latest }: { latest: string }) {
  const { t } = useTranslation();
  const create = useCreateFilingRule();
  const dryRun = useDryRunFilingRule();
  const toast = useToast();
  const [template, setTemplate] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [result, setResult] = useState<FilingDryRun | null>(null);
  const value = template ?? latest;

  return (
    <Card title={t("admin.rules.newVersion")} className="preview-card">
      <p className="muted rule-tokens">{t("admin.rules.tokens")}</p>
      <form
        className="form-grid"
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate(
            { template: value, note },
            {
              onSuccess: () => {
                setTemplate(null);
                setNote("");
                setResult(null);
                toast.push({ title: t("admin.rules.created"), tone: "success" });
              },
            },
          );
        }}
      >
        <Field label={t("admin.rules.template")}>
          {(c) => (
            <Input
              {...c}
              className="rb-input--mono"
              spellCheck={false}
              value={value}
              onChange={(e) => {
                setTemplate(e.target.value);
                setResult(null);
              }}
            />
          )}
        </Field>
        <Field label={t("admin.tokens.note")}>
          {(c) => <Input {...c} value={note} onChange={(e) => setNote(e.target.value)} />}
        </Field>
        {create.error ? (
          <p className="form-error" role="alert">
            {apiMessage(create.error, t("common.error"))}
          </p>
        ) : null}
        <div className="inline-form">
          <Button
            type="button"
            variant="secondary"
            icon={<Eye size={16} />}
            loading={dryRun.isPending}
            onClick={() => dryRun.mutate({ template: value }, { onSuccess: setResult })}
          >
            {t("admin.rules.preview")}
          </Button>
          <Button type="submit" loading={create.isPending}>
            {t("admin.rules.create")}
          </Button>
        </div>
      </form>
      {dryRun.error ? (
        <p className="form-error" role="alert">
          {apiMessage(dryRun.error, t("common.error"))}
        </p>
      ) : null}
      {result ? (
        <>
          <p className="muted">
            {t("admin.rules.previewSummary", {
              count: result.items.length,
              folders: result.directories,
              conflicts: result.conflicts.length,
            })}
          </p>
          {result.warnings.length > 0 ? (
            <p className="muted">{result.warnings.join(" · ")}</p>
          ) : null}
          <ul className="preview-list">
            {result.items.slice(0, 50).map((item) => (
              <li key={item.asset_id}>
                <code>{item.current_dir ?? item.filename}</code>
                <span aria-hidden="true">→</span>
                <code>{item.path}</code>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </Card>
  );
}

function SettingsPanel() {
  const settings = useLibrarySettings();
  return settings.data ? <SettingsForm initial={settings.data} /> : null;
}

function SettingsForm({ initial }: { initial: LibrarySettings }) {
  const { t } = useTranslation();
  const save = useSaveLibrarySettings();
  const toast = useToast();
  const [form, setForm] = useState({
    windows: initial.path_roots?.windows ?? "",
    macos: initial.path_roots?.macos ?? "",
    linux: initial.path_roots?.linux ?? "",
  });
  return (
    <Card title={t("admin.settings.paths")}>
      <p className="muted">{t("setup.paths.lead")}</p>
      <form
        className="form-grid"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate(
            {
              path_roots: {
                windows: form.windows.trim() || null,
                macos: form.macos.trim() || null,
                linux: form.linux.trim() || null,
              },
              central_meta: initial.central_meta,
              trash_retention_days: initial.trash_retention_days,
            },
            { onSuccess: () => toast.push({ title: t("common.saved"), tone: "success" }) },
          );
        }}
      >
        {(["windows", "macos", "linux"] as const).map((os) => (
          <Field key={os} label={t(`os.${os}`)} hint={t(`setup.paths.hint.${os}`)}>
            {(c) => (
              <Input
                {...c}
                className="rb-input--mono"
                spellCheck={false}
                placeholder={t(`setup.paths.example.${os}`)}
                value={form[os]}
                onChange={(e) => setForm({ ...form, [os]: e.target.value })}
              />
            )}
          </Field>
        ))}
        <div>
          <Button type="submit" loading={save.isPending}>
            {t("common.save")}
          </Button>
        </div>
      </form>
    </Card>
  );
}

function BackupsPanel() {
  const { t } = useTranslation();
  const backups = useBackups();
  const create = useCreateBackup();
  const toast = useToast();
  return (
    <>
      <div className="panel-head">
        <p className="muted">{t("admin.backups.lead")}</p>
        <Button
          icon={<DatabaseBackup size={16} />}
          loading={create.isPending}
          onClick={() =>
            create.mutate(undefined, {
              onSuccess: () => toast.push({ title: t("admin.backups.done"), tone: "success" }),
            })
          }
        >
          {t("admin.backups.now")}
        </Button>
      </div>
      <Card flush>
        <DataTable
          label={t("admin.backups.title")}
          rows={backups.data ?? []}
          columns={[
            {
              key: "name",
              header: t("admin.backups.file"),
              width: "minmax(0, 2fr)",
              cell: (b) => <code>{b.name}</code>,
            },
            { key: "kind", header: t("admin.backups.kind"), width: "110px", cell: (b) => b.kind },
            {
              key: "date",
              header: t("admin.tokens.created"),
              width: "170px",
              cell: (b) => formatDateTime(b.created_at),
            },
            {
              key: "size",
              header: t("library.col.size"),
              width: "100px",
              align: "end",
              cell: (b) => formatBytes(b.size),
            },
          ]}
          getRowId={(b) => b.name}
          rowHeight={52}
          empty={
            <EmptyState
              title={t("admin.backups.empty")}
              description={t("admin.backups.emptyText")}
            />
          }
        />
      </Card>
    </>
  );
}

export function AdminPage() {
  const { t } = useTranslation();
  const { tab = "users" } = useSearch({ from: "/_app/admin" });
  const navigate = useNavigate();
  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">{t("admin.title")}</h1>
          <p className="page__lead">{t("admin.lead")}</p>
        </div>
      </header>
      <Tabs
        value={tab}
        onValueChange={(value) =>
          void navigate({ to: "/admin", search: { tab: value as AdminTab }, replace: true })
        }
      >
        <TabsList aria-label={t("admin.sections")}>
          <TabsTrigger value="users">{t("admin.tabs.users")}</TabsTrigger>
          <TabsTrigger value="workers">{t("admin.tabs.workers")}</TabsTrigger>
          <TabsTrigger value="rules">{t("admin.tabs.rules")}</TabsTrigger>
          <TabsTrigger value="settings">{t("admin.tabs.settings")}</TabsTrigger>
          <TabsTrigger value="backups">{t("admin.tabs.backups")}</TabsTrigger>
          <TabsTrigger value="search">{t("admin.tabs.search")}</TabsTrigger>
          <TabsTrigger value="mcp">{t("admin.tabs.mcp")}</TabsTrigger>
          <TabsTrigger value="trash">{t("admin.tabs.trash")}</TabsTrigger>
          <TabsTrigger value="migration">{t("admin.tabs.migration")}</TabsTrigger>
          <TabsTrigger value="tokens">{t("admin.tabs.tokens")}</TabsTrigger>
          <TabsTrigger value="audit">{t("admin.tabs.audit")}</TabsTrigger>
        </TabsList>
        <TabsContent value="users">
          <UsersPanel />
        </TabsContent>
        <TabsContent value="workers">
          <TokensPanel />
        </TabsContent>
        <TabsContent value="rules">
          <RulesPanel />
        </TabsContent>
        <TabsContent value="settings">
          <SettingsPanel />
        </TabsContent>
        <TabsContent value="backups">
          <BackupsPanel />
        </TabsContent>
        <TabsContent value="search">
          <SearchAdmin />
        </TabsContent>
        <TabsContent value="mcp">
          <McpAdmin />
        </TabsContent>
        <TabsContent value="trash">
          <TrashAdmin />
        </TabsContent>
        <TabsContent value="migration">
          <MigrationAdmin />
        </TabsContent>
        <TabsContent value="tokens">
          <TokensAdmin />
        </TabsContent>
        <TabsContent value="audit">
          <AuditAdmin />
        </TabsContent>
      </Tabs>
    </div>
  );
}
