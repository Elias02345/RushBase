import { useQueryClient } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button, Dialog, ProgressBar } from "@rushbase/ui";

import { useBridge } from "../bridge/context";
import type { ModelEvent } from "../bridge/types";
import { formatBytes } from "../lib/format";

interface Progress {
  running: boolean;
  file: string | null;
  bytes: number;
  total: number;
  verifying: boolean;
  done: string[];
  error: string | null;
  finished: boolean;
}

const START: Progress = {
  running: false,
  file: null,
  bytes: 0,
  total: 0,
  verifying: false,
  done: [],
  error: null,
  finished: false,
};

/** Downloads the pinned models and inference backends (`rushbase models pull`, SHA-256 checked). */
export function ModelsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t, i18n } = useTranslation();
  const bridge = useBridge();
  const client = useQueryClient();
  const [progress, setProgress] = useState<Progress>(START);

  useEffect(() => {
    if (!open) return;
    const offs: (() => void)[] = [];
    let alive = true;
    void bridge
      .onStream(({ name, line }) => {
        if (name !== "models") return;
        let event: ModelEvent | undefined;
        try {
          event = (JSON.parse(line) as { data?: ModelEvent }).data;
        } catch {
          return;
        }
        if (!event) return;
        setProgress((p) => {
          switch (event.event) {
            case "download_started":
              return {
                ...p,
                file: event.file ?? null,
                bytes: 0,
                total: event.total ?? 0,
                verifying: false,
              };
            case "download_progress":
              return {
                ...p,
                file: event.file ?? p.file,
                bytes: event.bytes ?? p.bytes,
                total: event.total ?? p.total,
              };
            case "verify_started":
              return { ...p, verifying: true, file: event.file ?? p.file };
            case "model_done":
              return { ...p, verifying: false, done: [...p.done, String(event.id)] };
            case "error":
              return { ...p, error: event.message ?? "error" };
            default:
              return p;
          }
        });
      })
      .then((off) => (alive ? offs.push(off) : off()));
    void bridge
      .onStreamExit(({ name, code }) => {
        if (name !== "models") return;
        setProgress((p) => ({
          ...p,
          running: false,
          finished: code === 0,
          error: code === 0 ? null : (p.error ?? `exit ${code}`),
        }));
        void client.invalidateQueries({ queryKey: ["overview"] });
      })
      .then((off) => (alive ? offs.push(off) : off()));
    return () => {
      alive = false;
      offs.forEach((off) => off());
    };
  }, [bridge, client, open]);

  const start = useCallback(async () => {
    setProgress({ ...START, running: true });
    try {
      await bridge.streamStart("models", []);
    } catch (cause) {
      setProgress({ ...START, error: String(cause) });
    }
  }, [bridge]);

  const percent = progress.total > 0 ? (progress.bytes / progress.total) * 100 : null;
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("models.title")}
      description={t("models.description")}
      closeLabel={t("common.close")}
      footer={
        progress.running ? (
          <Button variant="secondary" onClick={() => void bridge.streamStop("models")}>
            {t("models.cancel")}
          </Button>
        ) : (
          <>
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              {t("common.close")}
            </Button>
            {!progress.finished ? (
              <Button icon={<Download size={16} />} onClick={() => void start()}>
                {progress.error ? t("models.retry") : t("models.start")}
              </Button>
            ) : null}
          </>
        )
      }
    >
      <div className="dk-models" aria-live="polite">
        {progress.file ? (
          <>
            <p className="dk-mono">{progress.file}</p>
            <ProgressBar
              value={progress.verifying ? null : percent}
              label={t("models.progress")}
              tone="processing"
            />
            <p className="dk-muted">
              {progress.verifying
                ? t("models.verifying")
                : `${formatBytes(progress.bytes, i18n.language)} / ${formatBytes(progress.total, i18n.language)}`}
            </p>
          </>
        ) : (
          <p className="dk-muted">{progress.finished ? t("models.finished") : t("models.idle")}</p>
        )}
        {progress.done.length > 0 ? (
          <p>{t("models.installed", { list: progress.done.join(", ") })}</p>
        ) : null}
        {progress.finished ? <p className="dk-ok">{t("models.finished")}</p> : null}
        {progress.error ? (
          <p role="alert" className="dk-error">
            {progress.error}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
