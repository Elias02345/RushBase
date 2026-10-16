import { useTranslation } from "react-i18next";

import { Card } from "@rushbase/ui";

import type { LogEntry } from "../state/runReducer";

const SHOWN = new Set([
  "job_done",
  "job_failed",
  "job_released",
  "claim_failed",
  "lease_lost",
  "stopped",
  "started",
  "report_failed",
]);

function line(
  entry: LogEntry,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  const { event } = entry.event;
  const stage = entry.event.stage ? t(`stage.${entry.event.stage}`) : "";
  switch (event) {
    case "job_done":
      return t("log.done", { stage, seconds: ((entry.event.duration_ms ?? 0) / 1000).toFixed(1) });
    case "job_failed":
      return t("log.failed", { stage, error: String(entry.event.error ?? "").slice(0, 120) });
    case "job_released":
      return t("log.released", { stage });
    case "stopped":
      return t("log.stopped", { reason: entry.event.reason ?? "" });
    case "started":
      return t("log.started");
    default:
      return t("log.other", { event });
  }
}

/** The most recent worker events, newest first. */
export function EventLog({ log }: { log: LogEntry[] }) {
  const { t, i18n } = useTranslation();
  const entries = log
    .filter((e) => SHOWN.has(e.event.event))
    .slice(-12)
    .reverse();
  return (
    <Card title={t("log.title")}>
      {entries.length === 0 ? (
        <p className="dk-muted">{t("log.empty")}</p>
      ) : (
        <ul className="dk-log">
          {entries.map((entry) => (
            <li
              key={`${entry.at}-${entry.event.job_id ?? entry.event.event}`}
              data-failed={entry.event.event === "job_failed" || undefined}
            >
              <time>{new Date(entry.at).toLocaleTimeString(i18n.language)}</time>
              <span>{line(entry, t)}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
