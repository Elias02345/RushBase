import {
  Button,
  Card,
  Checkbox,
  DataTable,
  Dialog,
  EmptyState,
  Field,
  Input,
  Select,
  StatusChip,
  useToast,
  type DataColumn,
} from "@rushbase/ui";
import { Copy, Plus } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { PersonalToken } from "../../api/contracts";
import {
  useCreatePersonalToken,
  usePersonalTokens,
  useRevokePersonalToken,
} from "../../api/finalQueries";
import { formatDateTime, formatRelative } from "../../lib/format";

const NEVER = "never";
const LIFETIMES = [NEVER, "30", "90", "365"] as const;

/** State of a token: revoked, expired, or active (expiry is enforced by the server). */
function tokenState(token: PersonalToken): "revoked" | "expired" | "active" {
  if (token.revoked_at) return "revoked";
  if (token.expires_at && Date.parse(token.expires_at) <= Date.now()) return "expired";
  return "active";
}

/** Personal API tokens of the signed-in user (CLI, companion app, scripts). */
export function TokensAdmin() {
  const { t } = useTranslation();
  const toast = useToast();
  const tokens = usePersonalTokens();
  const create = useCreatePersonalToken();
  const revoke = useRevokePersonalToken();
  const [name, setName] = useState("");
  const [lifetime, setLifetime] = useState<string>(NEVER);
  const [readOnly, setReadOnly] = useState(false);
  const [created, setCreated] = useState<string | null>(null);

  const columns: DataColumn<PersonalToken>[] = [
    { key: "name", header: t("tokens.name"), width: "minmax(0, 1.4fr)", cell: (x) => x.name },
    {
      key: "prefix",
      header: t("tokens.prefix"),
      width: "110px",
      cell: (x) => <code>{x.prefix}…</code>,
    },
    {
      key: "scope",
      header: t("tokens.scope"),
      width: "120px",
      cell: (x) =>
        x.scopes.includes("write") || x.scopes.length === 0
          ? t("tokens.full")
          : t("tokens.readOnly"),
    },
    {
      key: "used",
      header: t("tokens.lastUsed"),
      width: "130px",
      cell: (x) => (x.last_used_at ? formatRelative(x.last_used_at) : t("tokens.never")),
    },
    {
      key: "expires",
      header: t("tokens.expires"),
      width: "170px",
      cell: (x) => {
        const state = tokenState(x);
        if (state === "revoked")
          return (
            <StatusChip tone="neutral" plain>
              {t("tokens.revoked")}
            </StatusChip>
          );
        if (state === "expired")
          return (
            <StatusChip tone="quarantine" plain>
              {t("tokens.expired")}
            </StatusChip>
          );
        return x.expires_at ? formatDateTime(x.expires_at) : t("tokens.noExpiry");
      },
    },
    {
      key: "actions",
      header: "",
      width: "110px",
      align: "end",
      cell: (x) =>
        tokenState(x) === "revoked" ? null : (
          <Button size="sm" variant="ghost" onClick={() => revoke.mutate(x.id)}>
            {t("tokens.revoke")}
          </Button>
        ),
    },
  ];

  return (
    <>
      <div className="panel-head">
        <p className="muted">{t("tokens.lead")}</p>
        <form
          className="inline-form"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate(
              {
                name: name.trim(),
                readOnly,
                expiresInDays: lifetime === NEVER ? null : Number(lifetime),
              },
              {
                onSuccess: (result) => {
                  setCreated(result.token);
                  setName("");
                },
                onError: () => toast.push({ title: t("common.error"), tone: "error" }),
              },
            );
          }}
        >
          <Field label={t("tokens.name")}>
            {(control) => (
              <Input
                {...control}
                placeholder={t("tokens.namePlaceholder")}
                value={name}
                maxLength={100}
                onChange={(e) => setName(e.target.value)}
              />
            )}
          </Field>
          <Field label={t("tokens.lifetime")}>
            {({ id }) => (
              <Select
                id={id}
                value={lifetime}
                onValueChange={setLifetime}
                options={LIFETIMES.map((value) => ({
                  value,
                  label: t(`tokens.lifetimes.${value}`),
                }))}
              />
            )}
          </Field>
          <Checkbox
            checked={readOnly}
            onCheckedChange={setReadOnly}
            label={t("tokens.readOnlyOption")}
          />
          <Button
            type="submit"
            icon={<Plus size={16} />}
            loading={create.isPending}
            disabled={name.trim() === ""}
          >
            {t("tokens.create")}
          </Button>
        </form>
      </div>
      <Card flush>
        <DataTable
          label={t("tokens.title")}
          rows={tokens.data ?? []}
          columns={columns}
          getRowId={(x) => x.id}
          rowHeight={56}
          empty={<EmptyState title={t("tokens.empty")} description={t("tokens.emptyText")} />}
        />
      </Card>
      <Dialog
        open={created !== null}
        onOpenChange={(open) => !open && setCreated(null)}
        title={t("tokens.createdTitle")}
        description={t("tokens.createdText")}
        closeLabel={t("common.close")}
        footer={
          <Button
            icon={<Copy size={16} />}
            onClick={() => {
              void navigator.clipboard.writeText(created ?? "");
              toast.push({ title: t("common.copied"), tone: "success" });
            }}
          >
            {t("tokens.copy")}
          </Button>
        }
      >
        <code className="codeblock">{created}</code>
      </Dialog>
    </>
  );
}
