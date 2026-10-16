import { Card, EmptyState, ProgressRing, Skeleton, StatusChip } from "@rushbase/ui";
import { Link } from "@tanstack/react-router";
import { CloudOff, Cpu, FolderInput, FolderTree, ScanSearch } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { hasRole, useBatches, useMe, useStats, useStatus, useWorkers } from "../api/queries";
import type { SystemStatus } from "../api/types";
import { formatBytes, formatNumber, formatRelative } from "../lib/format";
import { batchTone } from "../lib/status";

function sum(values: Iterable<number>): number {
  let total = 0;
  for (const v of values) total += v;
  return total;
}

function Station({
  icon,
  title,
  children,
  ring,
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
  ring?: ReactNode;
}) {
  return (
    <div className="station">
      <div className="station__head">
        <span className="station__icon" aria-hidden="true">
          {icon}
        </span>
        <h3 className="station__title">{title}</h3>
      </div>
      {ring}
      <div className="station__counts">{children}</div>
    </div>
  );
}

function Count({
  tone,
  n,
  label,
}: {
  tone: Parameters<typeof StatusChip>[0]["tone"];
  n: number;
  label: string;
}) {
  return (
    <div className="count">
      <span className="count__n">{formatNumber(n)}</span>
      <StatusChip tone={tone}>{label}</StatusChip>
    </div>
  );
}

/** Input -> Analysis -> Content: material flows from left to right; dots move while work runs. */
function PipelineFlow({ status }: { status: SystemStatus }) {
  const { t } = useTranslation();
  const jobs = Object.values(status.stages);
  const pending = sum(jobs.map((s) => s.pending_jobs));
  const done = sum(jobs.map((s) => s.jobs.done ?? 0));
  const progress = done + pending > 0 ? (done / (done + pending)) * 100 : 100;
  const running = !status.queue_paused && status.workers_online > 0 && pending > 0;
  const files = status.input_files;
  const assets = status.assets;
  return (
    <div className="pipeline" role="group" aria-label={t("dashboard.pipelineLabel")}>
      <Station icon={<FolderInput size={20} />} title={t("dashboard.input")}>
        <Count tone="copying" n={files.copying ?? 0} label={t("status.copying")} />
        <Count tone="waiting" n={files.stable ?? 0} label={t("status.waiting")} />
      </Station>
      <div
        className={`flow${files.copying || files.stable ? " flow--active" : ""}`}
        aria-hidden="true"
      >
        <i /> <i /> <i />
      </div>
      <Station
        icon={<ScanSearch size={20} />}
        title={t("dashboard.analysis")}
        ring={
          <ProgressRing
            value={progress}
            size={92}
            strokeWidth={9}
            tone="processing"
            label={t("dashboard.analysisProgress")}
          />
        }
      >
        <Count
          tone="processing"
          n={(assets.processing ?? 0) + (assets.ingesting ?? 0)}
          label={t("status.processing")}
        />
        <p className="station__note">{t("dashboard.jobsOpen", { count: pending })}</p>
      </Station>
      <div className={`flow${running ? " flow--active" : ""}`} aria-hidden="true">
        <i /> <i /> <i />
      </div>
      <Station icon={<FolderTree size={20} />} title={t("dashboard.content")}>
        <Count tone="filed" n={assets.filed ?? 0} label={t("status.filed")} />
        <Count tone="review" n={assets.review ?? 0} label={t("status.review")} />
        <Count tone="quarantine" n={assets.quarantined ?? 0} label={t("status.quarantine")} />
      </Station>
    </div>
  );
}

export function DashboardPage() {
  const { t } = useTranslation();
  const me = useMe().data;
  const status = useStatus();
  const stats = useStats();
  const batches = useBatches();
  const isAdmin = hasRole(me?.role, "admin");
  const workers = useWorkers(isAdmin);

  const hour = new Date().getHours();
  const greeting =
    hour < 11 ? "dashboard.morning" : hour < 18 ? "dashboard.afternoon" : "dashboard.evening";
  const s = status.data;
  const recent = [...(batches.data ?? [])]
    .sort((a, b) => Date.parse(b.first_seen_at) - Date.parse(a.first_seen_at))
    .slice(0, 5);
  const online = (workers.data ?? []).filter((w) => w.online);

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">{t(greeting, { name: me?.display_name ?? "" })}</h1>
          <p className="page__lead">
            {s
              ? t("dashboard.summary", {
                  filed: formatNumber(s.assets.filed ?? 0),
                  processing: formatNumber((s.assets.processing ?? 0) + (s.assets.ingesting ?? 0)),
                })
              : t("common.loading")}
          </p>
        </div>
        {s?.queue_paused ? (
          <StatusChip tone="waiting">{t("dashboard.queuePaused")}</StatusChip>
        ) : null}
      </header>

      <Card title={t("dashboard.pipeline")} className="dash-pipeline">
        {s ? <PipelineFlow status={s} /> : <Skeleton height={180} radius={16} />}
      </Card>

      <div className="dash-grid">
        <Card
          title={t("dashboard.workers")}
          actions={
            <Link to="/processing" search={{ tab: "workers" }} className="link">
              {t("dashboard.allWorkers")}
            </Link>
          }
        >
          {!s ? (
            <Skeleton height={90} />
          ) : s.workers_online === 0 ? (
            <EmptyState
              compact
              icon={<CloudOff size={24} />}
              title={t("dashboard.noWorkerTitle")}
              description={t("dashboard.noWorker")}
            />
          ) : (
            <div className="workers-online">
              <ProgressRing
                value={(s.workers_online / Math.max(1, s.workers_total)) * 100}
                size={72}
                strokeWidth={8}
                tone="filed"
                label={t("dashboard.workersOnline")}
              >
                {s.workers_online}/{s.workers_total}
              </ProgressRing>
              <ul className="plain-list">
                {online.length === 0 ? (
                  <li>{t("dashboard.workersOnlineText", { count: s.workers_online })}</li>
                ) : (
                  online.map((w) => (
                    <li key={w.id}>
                      <Cpu size={16} aria-hidden="true" /> <strong>{w.name}</strong>
                      <span className="muted">
                        {w.last_stage
                          ? ` · ${t(`stages.${w.last_stage}`, { defaultValue: w.last_stage })}`
                          : ""}
                      </span>
                    </li>
                  ))
                )}
              </ul>
            </div>
          )}
        </Card>

        <Card
          title={t("dashboard.recentBatches")}
          actions={
            <Link to="/processing" search={{ tab: "batches" }} className="link">
              {t("dashboard.allBatches")}
            </Link>
          }
        >
          {batches.isLoading ? (
            <Skeleton height={120} />
          ) : recent.length === 0 ? (
            <EmptyState
              compact
              title={t("dashboard.noBatches")}
              description={t("dashboard.noBatchesText")}
            />
          ) : (
            <ul className="plain-list plain-list--rows">
              {recent.map((b) => (
                <li key={b.id}>
                  <span className="plain-list__main">
                    <strong>{b.label}</strong>
                    <span className="muted">{formatRelative(b.first_seen_at)}</span>
                  </span>
                  <StatusChip tone={batchTone(b.status, b.paused)}>
                    {b.paused ? t("batch.paused") : t(`batch.${b.status}`)}
                  </StatusChip>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title={t("dashboard.storage")}>
          {stats.data ? (
            <div className="storage">
              <p className="storage__total">
                <strong>{formatBytes(stats.data.storage.total_bytes)}</strong>
                <span className="muted">
                  {" "}
                  {t("dashboard.storageIn", {
                    count: sum(stats.data.storage.by_media_type.map((m) => m.count)),
                  })}
                </span>
              </p>
              <div className="storage__bar" role="img" aria-label={t("dashboard.storageBar")}>
                {stats.data.storage.by_media_type.map((m, index) => (
                  <span
                    key={m.key}
                    style={{ flexGrow: m.bytes, ["--seg" as string]: `var(--rb-seg-${index % 4})` }}
                  />
                ))}
              </div>
              <ul className="legend">
                {stats.data.storage.by_media_type.map((m, index) => (
                  <li key={m.key}>
                    <i style={{ background: `var(--rb-seg-${index % 4})` }} aria-hidden="true" />
                    {t(`media.${m.key}`, { defaultValue: m.key })}
                    <span className="muted">{formatBytes(m.bytes)}</span>
                  </li>
                ))}
              </ul>
              {stats.data.duplicates.count > 0 ? (
                <p className="muted">
                  {t("dashboard.duplicates", {
                    count: stats.data.duplicates.count,
                    size: formatBytes(stats.data.duplicates.wasted_bytes),
                  })}
                </p>
              ) : null}
            </div>
          ) : (
            <Skeleton height={120} />
          )}
        </Card>
      </div>
    </div>
  );
}
