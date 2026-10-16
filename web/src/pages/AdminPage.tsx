import {
  Button,
  Card,
  DataTable,
  Dialog,
  EmptyState,
  Field,
  Input,
  Select,
  Switch,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  useToast,
  type DataColumn,
} from "@rushbase/ui";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { ArrowDown, ArrowUp, Copy, DatabaseBackup, Eye, Plus, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import type {
  EnrollmentTokenInfo,
  FilingPreview,
  FilingRule,
  FilingRulesDoc,
  MediaType,
  PathMapping,
} from "../api/contracts";
import {
  useBackups,
  useCreateBackup,
  useCreateEnrollment,
  useCreateUser,
  useEnrollmentTokens,
  useFilingRules,
  useMe,
  usePathMapping,
  usePreviewFilingRules,
  useRevokeEnrollment,
  useSaveFilingRules,
  useSavePathMapping,
  useUpdateUser,
  useUsers,
} from "../api/queries";
import type { Role, User } from "../api/types";
import { formatBytes, formatDateTime } from "../lib/format";
import { copyText } from "../lib/platform";
import type { AdminTab } from "../routes/search";

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
        x.revoked_at
          ? t("admin.tokens.revoked")
          : x.used_at
            ? t("admin.tokens.used")
            : Date.parse(x.expires_at) < Date.now()
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
          disabled={Boolean(x.revoked_at || x.used_at)}
          onClick={() => revoke.mutate(x.id)}
        >
          {t("admin.tokens.revoke")}
        </Button>
      ),
    },
  ];
  const command = created
    ? `rushbase enroll --server ${window.location.origin} --token ${created}`
    : "";

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

const MEDIA_OPTIONS: MediaType[] = ["video", "photo", "audio", "other"];
const ANY = "__any__";

function RulesPanel() {
  const rules = useFilingRules();
  return rules.data ? <RulesEditor initial={rules.data} /> : null;
}

function RulesEditor({ initial }: { initial: FilingRulesDoc }) {
  const { t } = useTranslation();
  const save = useSaveFilingRules();
  const preview = usePreviewFilingRules();
  const toast = useToast();
  const [doc, setDoc] = useState<FilingRulesDoc>(initial);
  const [result, setResult] = useState<FilingPreview | null>(null);

  const patch = (id: string, change: Partial<FilingRule>) =>
    setDoc({ ...doc, rules: doc.rules.map((r) => (r.id === id ? { ...r, ...change } : r)) });
  const move = (index: number, delta: number) => {
    const next = [...doc.rules];
    const [item] = next.splice(index, 1);
    if (!item) return;
    next.splice(index + delta, 0, item);
    setDoc({ ...doc, rules: next });
  };

  return (
    <>
      <div className="panel-head">
        <p className="muted">{t("admin.rules.lead")}</p>
        <div className="inline-form">
          <Button
            variant="secondary"
            icon={<Eye size={16} />}
            loading={preview.isPending}
            onClick={() => preview.mutate(doc, { onSuccess: setResult })}
          >
            {t("admin.rules.preview")}
          </Button>
          <Button
            loading={save.isPending}
            onClick={() =>
              save.mutate(doc, {
                onSuccess: () => toast.push({ title: t("common.saved"), tone: "success" }),
              })
            }
          >
            {t("common.save")}
          </Button>
        </div>
      </div>
      <p className="muted rule-tokens">{t("admin.rules.tokens")}</p>
      <ol className="rule-list">
        {doc.rules.map((rule, index) => (
          <li key={rule.id} className="rule">
            <div className="rule__top">
              <Input
                aria-label={t("admin.rules.name")}
                value={rule.name}
                onChange={(e) => patch(rule.id, { name: e.target.value })}
              />
              <Switch
                label={t("admin.rules.enabled")}
                checked={rule.enabled}
                onCheckedChange={(enabled) => patch(rule.id, { enabled })}
              />
              <Button
                size="sm"
                variant="ghost"
                aria-label={t("admin.rules.up")}
                disabled={index === 0}
                onClick={() => move(index, -1)}
              >
                <ArrowUp size={14} />
              </Button>
              <Button
                size="sm"
                variant="ghost"
                aria-label={t("admin.rules.down")}
                disabled={index === doc.rules.length - 1}
                onClick={() => move(index, 1)}
              >
                <ArrowDown size={14} />
              </Button>
              <Button
                size="sm"
                variant="ghost"
                aria-label={t("admin.rules.remove")}
                onClick={() => setDoc({ ...doc, rules: doc.rules.filter((r) => r.id !== rule.id) })}
              >
                <Trash2 size={14} />
              </Button>
            </div>
            <div className="rule__fields">
              <Field label={t("admin.rules.mediaType")}>
                {({ id }) => (
                  <Select
                    id={id}
                    value={rule.when.media_type ?? ANY}
                    onValueChange={(v) =>
                      patch(rule.id, {
                        when: { ...rule.when, media_type: v === ANY ? null : (v as MediaType) },
                      })
                    }
                    options={[
                      { value: ANY, label: t("admin.rules.any") },
                      ...MEDIA_OPTIONS.map((m) => ({ value: m, label: t(`media.${m}`) })),
                    ]}
                  />
                )}
              </Field>
              <Field label={t("admin.rules.camera")}>
                {(c) => (
                  <Input
                    {...c}
                    value={rule.when.camera_model ?? ""}
                    placeholder={t("admin.rules.any")}
                    onChange={(e) =>
                      patch(rule.id, {
                        when: { ...rule.when, camera_model: e.target.value || null },
                      })
                    }
                  />
                )}
              </Field>
              <Field label={t("admin.rules.batch")}>
                {(c) => (
                  <Input
                    {...c}
                    value={rule.when.batch_contains ?? ""}
                    placeholder={t("admin.rules.any")}
                    onChange={(e) =>
                      patch(rule.id, {
                        when: { ...rule.when, batch_contains: e.target.value || null },
                      })
                    }
                  />
                )}
              </Field>
              <Field label={t("admin.rules.target")}>
                {(c) => (
                  <Input
                    {...c}
                    className="rb-input--mono"
                    value={rule.target}
                    onChange={(e) => patch(rule.id, { target: e.target.value })}
                  />
                )}
              </Field>
            </div>
          </li>
        ))}
      </ol>
      <div className="panel-head">
        <Button
          variant="secondary"
          icon={<Plus size={16} />}
          onClick={() =>
            setDoc({
              ...doc,
              rules: [
                ...doc.rules,
                {
                  id: `r${Date.now()}`,
                  name: t("admin.rules.newName"),
                  enabled: true,
                  when: { media_type: null, camera_model: null, batch_contains: null },
                  target: "{year}/{month}",
                },
              ],
            })
          }
        >
          {t("admin.rules.add")}
        </Button>
        <Field label={t("admin.rules.fallback")} className="rule__fallback">
          {(c) => (
            <Input
              {...c}
              className="rb-input--mono"
              value={doc.fallback}
              onChange={(e) => setDoc({ ...doc, fallback: e.target.value })}
            />
          )}
        </Field>
      </div>
      {result ? (
        <Card title={t("admin.rules.previewTitle")} className="preview-card">
          <p className="muted">
            {t("admin.rules.previewSummary", {
              count: result.items.length,
              unmatched: result.unmatched,
            })}
          </p>
          <ul className="preview-list">
            {result.items.map((item) => (
              <li key={item.asset_id}>
                <code>{item.from_rel_path}</code>
                <span aria-hidden="true">→</span>
                <code>{item.to_rel_path}</code>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </>
  );
}

function SettingsPanel() {
  const mapping = usePathMapping();
  return mapping.data ? <SettingsForm initial={mapping.data} /> : null;
}

function SettingsForm({ initial }: { initial: PathMapping }) {
  const { t } = useTranslation();
  const save = useSavePathMapping();
  const toast = useToast();
  const [form, setForm] = useState({
    windows: initial.windows ?? "",
    macos: initial.macos ?? "",
    linux: initial.linux ?? "",
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
              windows: form.windows.trim() || null,
              macos: form.macos.trim() || null,
              linux: form.linux.trim() || null,
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
      </Tabs>
    </div>
  );
}
