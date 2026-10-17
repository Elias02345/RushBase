import {
  Button,
  Card,
  DataTable,
  Drawer,
  EmptyState,
  Field,
  Input,
  Select,
  StatusChip,
  type DataColumn,
} from "@rushbase/ui";
import { Filter, X } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { AuditLogEntry } from "../../api/contracts";
import { useAuditActions, useAuditLog, type AuditFilters } from "../../api/finalQueries";
import { formatDateTime } from "../../lib/format";

const ALL = "__all__";
const ACTOR_KINDS = ["user", "worker", "mcp", "system"] as const;

/** Start of the given calendar day (UTC) and of the day after it, for a date range. */
function dayRange(from: string, to: string): Pick<AuditFilters, "since" | "until"> {
  const result: Pick<AuditFilters, "since" | "until"> = {};
  if (from) result.since = `${from}T00:00:00Z`;
  if (to) {
    const next = new Date(`${to}T00:00:00Z`);
    next.setUTCDate(next.getUTCDate() + 1);
    result.until = next.toISOString();
  }
  return result;
}

function actorLabel(entry: AuditLogEntry): string {
  return entry.actor_name ?? entry.actor_kind;
}

function EntryDrawer({
  entry,
  onClose,
  onActor,
}: {
  entry: AuditLogEntry | null;
  onClose: () => void;
  onActor: (id: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <Drawer
      open={entry !== null}
      onOpenChange={(open) => !open && onClose()}
      title={entry?.action ?? ""}
      description={entry ? formatDateTime(entry.at) : undefined}
      closeLabel={t("common.close")}
      size="md"
    >
      {entry ? (
        <dl className="audit-detail">
          <dt>{t("audit.actor")}</dt>
          <dd>
            {actorLabel(entry)}{" "}
            <span className="muted">({t(`audit.kind.${entry.actor_kind}`)})</span>
            {entry.actor_id ? (
              <Button
                size="sm"
                variant="ghost"
                icon={<Filter size={14} />}
                onClick={() => {
                  onActor(entry.actor_id ?? "");
                  onClose();
                }}
              >
                {t("audit.onlyThisActor")}
              </Button>
            ) : null}
          </dd>
          <dt>{t("audit.target")}</dt>
          <dd>
            {entry.target_kind ? (
              <code>
                {entry.target_kind}
                {entry.target_id ? ` / ${entry.target_id}` : ""}
              </code>
            ) : (
              "–"
            )}
          </dd>
          <dt>{t("audit.ip")}</dt>
          <dd>{entry.ip ?? "–"}</dd>
          <dt>{t("audit.id")}</dt>
          <dd>
            <code>{entry.id}</code>
          </dd>
          <dt>{t("audit.details")}</dt>
          <dd>
            <pre className="codeblock audit-detail__json">
              {JSON.stringify(entry.details, null, 2)}
            </pre>
          </dd>
        </dl>
      ) : null}
    </Drawer>
  );
}

/** Read-only viewer of the audit log with filters and keyset paging. */
export function AuditAdmin() {
  const { t } = useTranslation();
  const [actorKind, setActorKind] = useState(ALL);
  const [actorId, setActorId] = useState("");
  const [action, setAction] = useState(ALL);
  const [targetKind, setTargetKind] = useState("");
  const [targetId, setTargetId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [open, setOpen] = useState<AuditLogEntry | null>(null);

  const filters = useMemo<AuditFilters>(
    () => ({
      ...(actorKind !== ALL ? { actor_kind: actorKind } : {}),
      ...(actorId ? { actor_id: actorId } : {}),
      ...(action !== ALL ? { action } : {}),
      ...(targetKind.trim() ? { target_kind: targetKind.trim() } : {}),
      ...(targetId.trim() ? { target_id: targetId.trim() } : {}),
      ...dayRange(from, to),
    }),
    [actorKind, actorId, action, targetKind, targetId, from, to],
  );
  const log = useAuditLog(filters);
  const actions = useAuditActions();
  const rows = useMemo(() => log.data?.pages.flatMap((page) => page.items) ?? [], [log.data]);
  const filtered = Object.keys(filters).length > 0;

  const columns: DataColumn<AuditLogEntry>[] = [
    { key: "at", header: t("audit.at"), width: "170px", cell: (r) => formatDateTime(r.at) },
    {
      key: "actor",
      header: t("audit.actor"),
      width: "minmax(0, 1fr)",
      cell: (r) => (
        <>
          {actorLabel(r)} <span className="muted">· {t(`audit.kind.${r.actor_kind}`)}</span>
        </>
      ),
    },
    {
      key: "action",
      header: t("audit.action"),
      width: "minmax(0, 1.3fr)",
      cell: (r) => <code>{r.action}</code>,
    },
    {
      key: "target",
      header: t("audit.target"),
      width: "minmax(0, 1.3fr)",
      cell: (r) =>
        r.target_kind ? `${r.target_kind}${r.target_id ? ` / ${r.target_id}` : ""}` : "",
    },
  ];

  return (
    <div className="audit-admin">
      <Card title={t("audit.title")}>
        <p className="muted">{t("audit.lead")}</p>
        <div className="audit-filters">
          <Field label={t("audit.actorKind")}>
            {({ id }) => (
              <Select
                id={id}
                value={actorKind}
                onValueChange={setActorKind}
                options={[
                  { value: ALL, label: t("audit.all") },
                  ...ACTOR_KINDS.map((kind) => ({ value: kind, label: t(`audit.kind.${kind}`) })),
                ]}
              />
            )}
          </Field>
          <Field label={t("audit.action")}>
            {({ id }) => (
              <Select
                id={id}
                value={action}
                onValueChange={setAction}
                options={[
                  { value: ALL, label: t("audit.all") },
                  ...(actions.data ?? []).map((name) => ({ value: name, label: name })),
                ]}
              />
            )}
          </Field>
          <Field label={t("audit.targetKind")}>
            {(control) => (
              <Input
                {...control}
                value={targetKind}
                onChange={(e) => setTargetKind(e.target.value)}
              />
            )}
          </Field>
          <Field label={t("audit.targetId")}>
            {(control) => (
              <Input {...control} value={targetId} onChange={(e) => setTargetId(e.target.value)} />
            )}
          </Field>
          <Field label={t("audit.from")}>
            {(control) => (
              <Input
                {...control}
                type="date"
                value={from}
                max={to || undefined}
                onChange={(e) => setFrom(e.target.value)}
              />
            )}
          </Field>
          <Field label={t("audit.to")}>
            {(control) => (
              <Input
                {...control}
                type="date"
                value={to}
                min={from || undefined}
                onChange={(e) => setTo(e.target.value)}
              />
            )}
          </Field>
        </div>
        {actorId || filtered ? (
          <div className="audit-active">
            {actorId ? (
              <button type="button" className="filter-chip" onClick={() => setActorId("")}>
                {t("audit.actorChip", { id: actorId.slice(0, 8) })}
                <X size={14} aria-label={t("library.removeFilter")} />
              </button>
            ) : null}
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setActorKind(ALL);
                setActorId("");
                setAction(ALL);
                setTargetKind("");
                setTargetId("");
                setFrom("");
                setTo("");
              }}
            >
              {t("audit.reset")}
            </Button>
          </div>
        ) : null}
      </Card>
      <Card flush>
        <DataTable
          label={t("audit.title")}
          rows={rows}
          columns={columns}
          getRowId={(r) => r.id}
          rowHeight={48}
          maxHeight={560}
          onRowClick={setOpen}
          selectedId={open?.id ?? null}
          onNearEnd={() => {
            if (log.hasNextPage && !log.isFetchingNextPage) void log.fetchNextPage();
          }}
          empty={
            <EmptyState
              title={t("audit.empty")}
              description={filtered ? t("audit.emptyFiltered") : t("audit.emptyText")}
            />
          }
        />
        <div className="audit-foot">
          <StatusChip tone="neutral" plain>
            {t("audit.loaded", { count: rows.length })}
          </StatusChip>
          {log.hasNextPage ? (
            <Button
              size="sm"
              variant="secondary"
              loading={log.isFetchingNextPage}
              onClick={() => void log.fetchNextPage()}
            >
              {t("audit.more")}
            </Button>
          ) : null}
        </div>
      </Card>
      <EntryDrawer entry={open} onClose={() => setOpen(null)} onActor={setActorId} />
    </div>
  );
}
