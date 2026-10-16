import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Download,
  Play,
  RefreshCw,
  Square,
  WifiOff,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button, Card, EmptyState, ProgressRing, Skeleton, StatusChip } from "@rushbase/ui";

import type { Overview } from "../bridge/types";
import { EventLog } from "../components/EventLog";
import { HardwareCard } from "../components/HardwareCard";
import { ModelsDialog } from "../components/ModelsDialog";
import { RunOptionsCard } from "../components/RunOptionsCard";
import { clockIn, formatDuration, formatNumber } from "../lib/format";
import { useGpuStats, useOverview } from "../state/queries";
import { useRun } from "../state/run";
import { currentStage, etaSeconds, jobsPerMinute } from "../state/runReducer";

interface Props {
  onSetup: () => void;
  onSettings: () => void;
}

/** Home: backlog, hardware, one-click processing, live progress. */
export function ProcessorPage({ onSetup, onSettings }: Props) {
  const { t } = useTranslation();
  const overview = useOverview();
  const client = useQueryClient();
  const [modelsOpen, setModelsOpen] = useState(false);
  const { state, options, setOptions, start, error } = useRun();
  const gpu = useGpuStats();

  if (overview.isPending) {
    return (
      <div className="dk-page" aria-busy="true">
        <Skeleton height={220} />
      </div>
    );
  }
  if (overview.isError) {
    return (
      <div className="dk-page">
        <EmptyState
          icon={<AlertTriangle />}
          title={t("processor.cliMissing")}
          description={overview.error.message}
          action={
            <Button variant="secondary" onClick={onSettings}>
              {t("nav.settings")}
            </Button>
          }
        />
      </div>
    );
  }

  const data = overview.data;
  const busy =
    state.phase === "running" ||
    state.phase === "starting" ||
    state.phase === "stopping" ||
    state.phase === "waiting";
  const refresh = () => void client.invalidateQueries({ queryKey: ["overview"] });
  return (
    <div className="dk-page dk-processor">
      <section className="dk-hero" aria-live="polite">
        {busy || state.phase === "finished" ? (
          <RunHero overview={data} />
        ) : (
          <BacklogHero
            overview={data}
            onSetup={onSetup}
            onSettings={onSettings}
            onRefresh={refresh}
            onStart={() => void start(data.backlog, data.runnable_stages)}
          />
        )}
        {error ? (
          <p role="alert" className="dk-error">
            {error}
          </p>
        ) : null}
      </section>

      {data.models.missing.length > 0 ? (
        <Card className="dk-callout">
          <Download size={20} aria-hidden="true" />
          <div>
            <strong>{t("processor.modelsMissing", { count: data.models.missing.length })}</strong>
            <p className="dk-muted">{t("processor.modelsMissingHint")}</p>
          </div>
          <Button variant="secondary" onClick={() => setModelsOpen(true)}>
            {t("processor.loadModels")}
          </Button>
        </Card>
      ) : null}

      <div className="dk-grid">
        <HardwareCard hardware={data.hardware} models={data.models} />
        <RunOptionsCard
          options={options}
          onChange={setOptions}
          runnable={data.runnable_stages}
          disabled={busy}
          hasTemperature={gpu.data?.temperature_c != null}
        />
      </div>
      <EventLog log={state.log} />
      <ModelsDialog open={modelsOpen} onOpenChange={setModelsOpen} />
    </div>
  );
}

function BacklogHero({
  overview,
  onSetup,
  onSettings,
  onRefresh,
  onStart,
}: {
  overview: Overview;
  onSetup: () => void;
  onSettings: () => void;
  onRefresh: () => void;
  onStart: () => void;
}) {
  const { t, i18n } = useTranslation();
  const blocking = overview.issues.find((i) => i.code !== "models_missing");
  if (blocking) {
    const setup = blocking.code === "no_server" || blocking.code === "not_enrolled";
    return (
      <EmptyState
        icon={<WifiOff />}
        title={t(`issue.${blocking.code}.title`)}
        description={`${t(`issue.${blocking.code}.fix`)} ${blocking.code === "server_unreachable" || blocking.code === "mount" ? blocking.message : ""}`.trim()}
        action={
          <div className="dk-actions">
            {setup ? <Button onClick={onSetup}>{t("issue.startSetup")}</Button> : null}
            {!setup ? (
              <Button icon={<RefreshCw size={16} />} onClick={onRefresh}>
                {t("common.retry")}
              </Button>
            ) : null}
            {!setup ? (
              <Button variant="secondary" onClick={onSettings}>
                {t("nav.settings")}
              </Button>
            ) : null}
          </div>
        }
      />
    );
  }
  const { totals, estimate } = overview;
  const stages = Object.entries(overview.backlog?.stages ?? {}).filter(
    ([, s]) => s.pending_jobs > 0,
  );
  if (totals.jobs === 0) {
    return (
      <EmptyState
        icon={<CheckCircle2 />}
        title={t("hero.allDone")}
        description={
          overview.last_run
            ? t("hero.lastRun", {
                when: new Date(overview.last_run.finished_at).toLocaleString(i18n.language),
                count: overview.last_run.completed,
              })
            : t("hero.allDoneHint")
        }
        action={
          <Button variant="secondary" icon={<RefreshCw size={16} />} onClick={onRefresh}>
            {t("common.refresh")}
          </Button>
        }
      />
    );
  }
  const measured =
    estimate &&
    Object.values(estimate.stages)
      .filter((s) => s.pending_jobs > 0)
      .every((s) => s.source === "history");
  return (
    <div className="dk-hero__body">
      <div className="dk-hero__numbers">
        <p className="dk-hero__eyebrow">{t("hero.eyebrow")}</p>
        <h1 className="dk-hero__title">
          {t("hero.files", { count: totals.files })}
          <span className="dk-hero__sub">
            {t("hero.hours", { hours: formatNumber(totals.hours, i18n.language) })}
          </span>
        </h1>
        <div className="dk-chips" aria-label={t("hero.perStage")}>
          {stages.map(([stage, value]) => (
            <StatusChip key={stage} tone="waiting">
              {t(`stage.${stage}`)} · {value.pending_jobs}
            </StatusChip>
          ))}
        </div>
        {estimate ? (
          <p className="dk-hero__estimate">
            <Clock size={16} aria-hidden="true" />
            {t("hero.estimate", {
              duration: formatDuration(estimate.total_seconds),
              at: clockIn(estimate.total_seconds),
            })}
            <span className="dk-muted">
              {" "}
              · {measured ? t("hero.estimateMeasured") : t("hero.estimateDefault")}
            </span>
          </p>
        ) : null}
      </div>
      <Button
        size="lg"
        icon={<Play size={20} />}
        onClick={onStart}
        disabled={overview.runnable_stages.length === 0}
      >
        {t("processor.start")}
      </Button>
    </div>
  );
}

function RunHero({ overview }: { overview: Overview }) {
  const { t, i18n } = useTranslation();
  const { state, stop, dismiss } = useRun();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const total = Math.max(state.totalJobs, state.done + state.failed);
  const percent = total > 0 ? ((state.done + state.failed) / total) * 100 : 0;
  const rate = jobsPerMinute(state, now);
  const eta = etaSeconds(state, now);
  const stage = currentStage(state);
  const finished = state.phase === "finished";
  const waiting = state.phase === "waiting";
  return (
    <div className="dk-hero__body">
      <ProgressRing
        value={percent}
        size={132}
        strokeWidth={12}
        label={t("run.progress")}
        tone={finished ? "filed" : "processing"}
      />
      <div className="dk-hero__numbers">
        <p className="dk-hero__eyebrow">
          {waiting
            ? t(state.note === "guard_temp" ? "run.waitingTemp" : "run.waitingIdle")
            : state.phase === "stopping"
              ? t("run.stopping")
              : finished
                ? t("run.finished", {
                    reason: state.reason
                      ? t(`run.reason.${state.reason}`, { defaultValue: state.reason })
                      : "",
                  })
                : t("run.running")}
        </p>
        <h1 className="dk-hero__title">
          {t("run.items", { done: state.done, total })}
          {stage ? (
            <span className="dk-hero__sub">
              {t("run.currentStage", { stage: t(`stage.${stage}`) })}
            </span>
          ) : null}
        </h1>
        <p className="dk-hero__estimate">
          {rate ? t("run.rate", { rate: formatNumber(rate, i18n.language) }) : t("run.rateUnknown")}
          {eta != null && !finished
            ? ` · ${t("run.eta", { duration: formatDuration(eta), at: clockIn(eta, now) })}`
            : ""}
          {state.failed > 0 ? ` · ${t("run.failed", { count: state.failed })}` : ""}
          {state.released > 0 ? ` · ${t("run.released", { count: state.released })}` : ""}
        </p>
        {finished ? (
          <p className="dk-muted">{t("run.backlogLeft", { count: overview.totals.jobs })}</p>
        ) : null}
      </div>
      {finished ? (
        <Button variant="secondary" onClick={dismiss}>
          {t("common.ok")}
        </Button>
      ) : (
        <Button
          variant="danger"
          icon={<Square size={18} />}
          loading={state.phase === "stopping"}
          onClick={() => void stop()}
        >
          {waiting ? t("run.cancel") : t("run.stop")}
        </Button>
      )}
    </div>
  );
}
