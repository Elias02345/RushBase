import {
  Button,
  Card,
  DataTable,
  Field,
  Select,
  Skeleton,
  StatusChip,
  Textarea,
  useToast,
  type DataColumn,
} from "@rushbase/ui";
import { Copy, Unplug } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { McpAuditEntry, McpClient } from "../../api/contracts";
import { useMcpActions, useMcpAudit, useMcpStatus } from "../../api/mcpQueries";
import { formatDateTime, formatRelative } from "../../lib/format";

const ALL = "__all__";

function Audit({ clients }: { clients: McpClient[] }) {
  const { t } = useTranslation();
  const [client, setClient] = useState(ALL);
  const [tool, setTool] = useState(ALL);
  const audit = useMcpAudit({
    client_id: client === ALL ? undefined : client,
    tool: tool === ALL ? undefined : tool,
  });
  const columns: DataColumn<McpAuditEntry>[] = [
    { key: "at", header: t("mcp.audit.at"), width: "170px", cell: (r) => formatDateTime(r.at) },
    { key: "client", header: t("mcp.audit.client"), cell: (r) => r.client_name },
    { key: "tool", header: t("mcp.audit.tool"), cell: (r) => <code>{r.tool}</code> },
    {
      key: "ms",
      header: t("mcp.audit.duration"),
      width: "90px",
      align: "end",
      cell: (r) => `${r.duration_ms} ms`,
    },
    {
      key: "size",
      header: t("mcp.audit.size"),
      width: "100px",
      align: "end",
      cell: (r) => r.result_chars.toLocaleString(),
    },
    {
      key: "ok",
      header: t("mcp.audit.result"),
      width: "120px",
      cell: (r) => (
        <StatusChip tone={r.ok ? "filed" : "quarantine"} plain>
          {r.ok ? t("mcp.audit.ok") : t("mcp.audit.failed")}
        </StatusChip>
      ),
    },
  ];
  return (
    <Card title={t("mcp.audit.title")}>
      <div className="mcp-filters">
        <Field label={t("mcp.audit.client")}>
          {({ id }) => (
            <Select
              id={id}
              value={client}
              onValueChange={setClient}
              options={[
                { value: ALL, label: t("mcp.audit.all") },
                ...clients.map((c) => ({ value: c.id, label: c.name })),
              ]}
            />
          )}
        </Field>
        <Field label={t("mcp.audit.tool")}>
          {({ id }) => (
            <Select
              id={id}
              value={tool}
              onValueChange={setTool}
              options={[
                { value: ALL, label: t("mcp.audit.all") },
                ...(audit.data?.tools ?? []).map((name) => ({ value: name, label: name })),
              ]}
            />
          )}
        </Field>
      </div>
      <DataTable
        label={t("mcp.audit.title")}
        rows={audit.data?.items ?? []}
        columns={columns}
        getRowId={(r) => r.id}
        empty={<p className="muted">{t("mcp.audit.empty")}</p>}
      />
    </Card>
  );
}

/** Connector URL, connected OAuth clients with revoke, extra redirect URIs and the call log. */
export function McpAdmin() {
  const { t } = useTranslation();
  const toast = useToast();
  const status = useMcpStatus();
  const { revoke, redirects } = useMcpActions();
  const [extra, setExtra] = useState<string | null>(null);
  if (status.isLoading) return <Skeleton height={240} radius={16} />;
  const data = status.data;
  if (!data) return null;
  const columns: DataColumn<McpClient>[] = [
    { key: "name", header: t("mcp.clients.name"), cell: (c) => <strong>{c.name}</strong> },
    {
      key: "scopes",
      header: t("mcp.clients.scopes"),
      width: "2fr",
      cell: (c) => (c.scopes.length ? c.scopes.join(" ") : "—"),
    },
    {
      key: "used",
      header: t("mcp.clients.lastUsed"),
      width: "150px",
      cell: (c) => (c.last_used_at ? formatRelative(c.last_used_at) : t("mcp.clients.never")),
    },
    {
      key: "state",
      header: t("mcp.clients.state"),
      width: "130px",
      cell: (c) => (
        <StatusChip tone={c.revoked_at ? "neutral" : "filed"} plain>
          {c.revoked_at ? t("mcp.clients.revoked") : t("mcp.clients.grants", { count: c.grants })}
        </StatusChip>
      ),
    },
    {
      key: "actions",
      header: "",
      width: "120px",
      align: "end",
      cell: (c) =>
        c.revoked_at ? null : (
          <Button
            size="sm"
            variant="ghost"
            icon={<Unplug size={14} />}
            onClick={() =>
              revoke.mutate(c.id, {
                onSuccess: () =>
                  toast.push({ title: t("mcp.clients.revokedToast"), tone: "success" }),
              })
            }
          >
            {t("mcp.clients.revoke")}
          </Button>
        ),
    },
  ];
  return (
    <div className="mcp-admin">
      <Card title={t("mcp.connector.title")}>
        {data.enabled && data.mcp_url ? (
          <>
            <p className="muted">{t("mcp.connector.lead")}</p>
            <div className="mcp-url">
              <code>{data.mcp_url}</code>
              <Button
                variant="secondary"
                icon={<Copy size={16} />}
                onClick={() => {
                  void navigator.clipboard.writeText(data.mcp_url ?? "");
                  toast.push({ title: t("mcp.connector.copied"), tone: "success" });
                }}
              >
                {t("mcp.connector.copy")}
              </Button>
            </div>
            <ol className="mcp-guide">
              <li>{t("mcp.guide.one")}</li>
              <li>{t("mcp.guide.two", { url: data.mcp_url })}</li>
              <li>{t("mcp.guide.three")}</li>
              <li>{t("mcp.guide.four")}</li>
            </ol>
            <p className="muted">{t("mcp.guide.paths")}</p>
          </>
        ) : (
          <div className="search-admin__notice" role="alert">
            <p>{t("mcp.connector.missing")}</p>
          </div>
        )}
        <p className="muted">
          {t("mcp.connector.limits", { tools: data.tools, rate: data.rate_limit_per_minute })}
        </p>
      </Card>
      <Card title={t("mcp.clients.title")} flush>
        <DataTable
          label={t("mcp.clients.title")}
          rows={data.clients}
          columns={columns}
          getRowId={(c) => c.id}
          empty={<p className="muted">{t("mcp.clients.empty")}</p>}
        />
      </Card>
      <Card title={t("mcp.redirects.title")}>
        <p className="muted">{t("mcp.redirects.lead")}</p>
        <Field label={t("mcp.redirects.label")}>
          {(control) => (
            <Textarea
              {...control}
              rows={3}
              value={extra ?? data.extra_redirect_uris.join("\n")}
              onChange={(event) => setExtra(event.target.value)}
            />
          )}
        </Field>
        <Button
          variant="secondary"
          disabled={extra === null}
          loading={redirects.isPending}
          onClick={() =>
            redirects.mutate(
              (extra ?? "")
                .split("\n")
                .map((line) => line.trim())
                .filter(Boolean),
              {
                onSuccess: () => {
                  setExtra(null);
                  toast.push({ title: t("mcp.redirects.saved"), tone: "success" });
                },
                onError: () => toast.push({ title: t("mcp.redirects.invalid"), tone: "error" }),
              },
            )
          }
        >
          {t("common.save")}
        </Button>
      </Card>
      <Audit clients={data.clients} />
    </div>
  );
}
