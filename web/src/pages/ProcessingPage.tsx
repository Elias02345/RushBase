import {
  Button,
  Card,
  DataTable,
  EmptyState,
  IconButton,
  Skeleton,
  StatusChip,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  useToast,
  type DataColumn,
} from "@rushbase/ui";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { Minus, Pause, Play, Plus, RotateCw, ShieldCheck, Unplug } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { AssetSummary } from "../api/contracts";
import {
  hasRole,
  useAssets,
  useBatchControl,
  useBatches,
  useMe,
  useQueuePaused,
  useRetryAsset,
  useRevokeWorker,
  useStatus,
  useWorkers,
} from "../api/queries";
import type { Batch, Worker } from "../api/types";
import { STAGE_ORDER } from "../api/types";
import { formatBytes, formatDurationWords, formatNumber, formatRelative } from "../lib/format";
import { batchTone } from "../lib/status";
import type { ProcessingTab } from "../routes/search";

function QueuePanel() {
  const { t } = useTranslation();
  const status = useStatus().data;
  if (!status) return <Skeleton height={240} radius={22} />;
  const stages = STAGE_ORDER.filter((name) => status.stages[name]);
  if (stages.length === 0) {
    return <EmptyState title={t("processing.queueEmpty")} description={t("processing.queueEmptyText")} />;
  }
  return (
    <ul className="stage-grid">
      {stages.map((name) => {
        const stage = status.stages[name];
        if (!stage) return null;
        const done = stage.jobs.done ?? 0;
        const failed = stage.jobs.failed ?? 0;
        const leased = stage.jobs.leased ?? 0;
        const queued = (stage.jobs.queued ?? 0) + (stage.jobs.blocked ?? 0);
        const all = Math.max(1, done + failed + leased + queued);
        return (
          <li key={name} className="stage-card">
            <div className="stage-card__head">
              <h3>{t(`stages.${name}`)}</h3>
              <span className="stage-card__n">{formatNumber(stage.pending_jobs)}</span>
            </div>
            <div className="stackbar" role="img" aria-label={t("processing.stageBar", { done, running: leased, queued, failed })}>
              <span style={{ flexGrow: done, background: "var(--rb-filed-solid)" }} />
              <span className="stackbar__run" style={{ flexGrow: leased }} />
              <span style={{ flexGrow: queued, background: "var(--rb-waiting-solid)" }} />
              <span style={{ flexGrow: failed, background: "var(--rb-quarantine-solid)" }} />
              <span style={{ flexGrow: all === 0 ? 1 : 0 }} />
            </div>
            <p className="muted">
              {t("processing.stageFacts", {
                bytes: formatBytes(stage.pending_bytes),
                duration: stage.pending_duration_ms ? formatDurationWords(stage.pending_duration_ms) : "–",
              })}
            </p>
          </li>
        );
      })}
    </ul>
  );
}

function BatchesPanel({ canEdit }: { canEdit: boolean }) {
  const { t } = useTranslation();
  const batches = useBatches();
  const { pause, priority } = useBatchControl();
  const columns: DataColumn<Batch>[] = [
    { key: "label", header: t("processing.batch"), width: "minmax(0, 2fr)", cell: (b) => <strong>{b.label}</strong> },
    {
      key: "status",
      header: t("library.col.status"),
      width: "140px",
      cell: (b) => (
        <StatusChip tone={batchTone(b.status, b.paused)}>
          {b.paused ? t("batch.paused") : t(`batch.${b.status}`)}
        </StatusChip>
      ),
    },
    { key: "seen", header: t("processing.firstSeen"), width: "150px", cell: (b) => formatRelative(b.first_seen_at) },
    {
      key: "priority",
      header: t("processing.priority"),
      width: "150px",
      cell: (b) => (
        <span className="stepper-inline">
          <IconButton
            label={t("processing.lower", { name: b.label })}
            icon={<Minus size={14} />}
            size="sm"
            disabled={!canEdit}
            onClick={() => priority.mutate({ id: b.id, value: Math.max(-100, b.priority - 5) })}
          />
          <span className="stepper-inline__n" aria-label={t("processing.priorityValue", { value: b.priority })}>
            {b.priority}
          </span>
          <IconButton
            label={t("processing.raise", { name: b.label })}
            icon={<Plus size={14} />}
            size="sm"
            disabled={!canEdit}
            onClick={() => priority.mutate({ id: b.id, value: Math.min(100, b.priority + 5) })}
          />
        </span>
      ),
    },
    {
      key: "actions",
      header: "",
      width: "130px",
      align: "end",
      cell: (b) => (
        <Button
          size="sm"
          variant="secondary"
          disabled={!canEdit || b.status === "done"}
          icon={b.paused ? <Play size={14} /> : <Pause size={14} />}
          onClick={() => pause.mutate({ id: b.id, paused: !b.paused })}
        >
          {b.paused ? t("processing.resume") : t("processing.pause")}
        </Button>
      ),
    },
  ];
  if (batches.isLoading) return <Skeleton height={240} radius={22} />;
  return (
    <Card flush>
      <DataTable
        label={t("processing.batches")}
        rows={batches.data ?? []}
        columns={columns}
        getRowId={(b) => b.id}
        rowHeight={60}
        empty={<EmptyState title={t("dashboard.noBatches")} description={t("dashboard.noBatchesText")} />}
      />
    </Card>
  );
}

function WorkersPanel({ isAdmin }: { isAdmin: boolean }) {
  const { t } = useTranslation();
  const workers = useWorkers(isAdmin);
  const revoke = useRevokeWorker();
  const status = useStatus().data;
  if (!isAdmin) {
    return (
      <Card>
        <p>{t("processing.workersCount", { online: status?.workers_online ?? 0, total: status?.workers_total ?? 0 })}</p>
        <p className="muted">{t("processing.workersAdminOnly")}</p>
      </Card>
    );
  }
  const columns: DataColumn<Worker>[] = [
    { key: "name", header: t("processing.worker"), width: "minmax(0, 1.4fr)", cell: (w) => <strong>{w.name}</strong> },
    { key: "platform", header: t("processing.platform"), cell: (w) => w.platform ?? "–" },
    {
      key: "status",
      header: t("library.col.status"),
      width: "130px",
      cell: (w) => (
        <StatusChip tone={w.online ? "filed" : "neutral"}>
          {w.online ? t("processing.online") : t("processing.offline")}
        </StatusChip>
      ),
    },
    { key: "seen", header: t("processing.lastSeen"), width: "150px", cell: (w) => formatRelative(w.last_seen_at) },
    {
      key: "stage",
      header: t("processing.lastStage"),
      width: "120px",
      cell: (w) => (w.last_stage ? t(`stages.${w.last_stage}`, { defaultValue: w.last_stage }) : "–"),
    },
    {
      key: "actions",
      header: "",
      width: "120px",
      align: "end",
      cell: (w) => (
        <Button size="sm" variant="ghost" icon={<Unplug size={14} />} onClick={() => revoke.mutate(w.id)}>
          {t("processing.revoke")}
        </Button>
      ),
    },
  ];
  return (
    <Card flush>
      <DataTable
        label={t("processing.workers")}
        rows={workers.data ?? []}
        columns={columns}
        getRowId={(w) => w.id}
        rowHeight={56}
        empty={<EmptyState title={t("dashboard.noWorkerTitle")} description={t("dashboard.noWorker")} />}
      />
    </Card>
  );
}

function QuarantinePanel({ canEdit }: { canEdit: boolean }) {
  const { t } = useTranslation();
  const toast = useToast();
  const assets = useAssets({ status: "quarantined" });
  const retry = useRetryAsset();
  const rows: AssetSummary[] = assets.data?.pages.flatMap((p) => p.items) ?? [];
  if (assets.isLoading) return <Skeleton height={200} radius={22} />;
  if (rows.length === 0) {
    return (
      <EmptyState
        icon={<ShieldCheck size={28} />}
        title={t("processing.quarantineEmpty")}
        description={t("processing.quarantineEmptyText")}
      />
    );
  }
  return (
    <Card flush>
      <ul className="quarantine-list">
        {rows.map((a) => (
          <li key={a.id}>
            <div>
              <strong>{a.title ?? a.filename}</strong>
              <div className="muted">{a.batch_label ?? ""} · {a.filename}</div>
            </div>
            <Button
              size="sm"
              variant="secondary"
              icon={<RotateCw size={14} />}
              disabled={!canEdit}
              loading={retry.isPending && retry.variables === a.id}
              onClick={() =>
                retry.mutate(a.id, {
                  onSuccess: () => toast.push({ title: t("processing.retryQueued"), tone: "success" }),
                })
              }
            >
              {t("processing.retry")}
            </Button>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function ProcessingPage() {
  const { t } = useTranslation();
  const { tab = "queue" } = useSearch({ from: "/_app/processing" });
  const navigate = useNavigate();
  const me = useMe().data;
  const status = useStatus().data;
  const queue = useQueuePaused();
  const isAdmin = hasRole(me?.role, "admin");
  const canEdit = hasRole(me?.role, "editor");

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">{t("processing.title")}</h1>
          <p className="page__lead">{t("processing.lead")}</p>
        </div>
        {isAdmin && status ? (
          <Button
            variant={status.queue_paused ? "primary" : "secondary"}
            icon={status.queue_paused ? <Play size={16} /> : <Pause size={16} />}
            loading={queue.isPending}
            onClick={() => queue.mutate(!status.queue_paused)}
          >
            {status.queue_paused ? t("processing.resumeQueue") : t("processing.pauseQueue")}
          </Button>
        ) : null}
      </header>
      <Tabs
        value={tab}
        onValueChange={(value) => void navigate({ to: "/processing", search: { tab: value as ProcessingTab }, replace: true })}
      >
        <TabsList aria-label={t("processing.sections")}>
          <TabsTrigger value="queue">{t("processing.tabs.queue")}</TabsTrigger>
          <TabsTrigger value="batches">{t("processing.tabs.batches")}</TabsTrigger>
          <TabsTrigger value="workers">{t("processing.tabs.workers")}</TabsTrigger>
          <TabsTrigger value="quarantine">{t("processing.tabs.quarantine")}</TabsTrigger>
        </TabsList>
        <TabsContent value="queue">
          <QueuePanel />
        </TabsContent>
        <TabsContent value="batches">
          <BatchesPanel canEdit={canEdit} />
        </TabsContent>
        <TabsContent value="workers">
          <WorkersPanel isAdmin={isAdmin} />
        </TabsContent>
        <TabsContent value="quarantine">
          <QuarantinePanel canEdit={canEdit} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
