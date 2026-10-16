import { Cpu, Thermometer, Zap } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge, Card, ProgressBar } from "@rushbase/ui";

import type { Overview } from "../bridge/types";
import { formatNumber } from "../lib/format";
import { useGpuStats } from "../state/queries";

/** GPU, VRAM, backend, profile and the model set of this PC, with live telemetry. */
export function HardwareCard({ hardware, models }: Pick<Overview, "hardware" | "models">) {
  const { t, i18n } = useTranslation();
  const { data: live } = useGpuStats();
  const total = live?.memory_total_mb ?? hardware.vram_total_mb;
  const used =
    live?.memory_used_mb ??
    (total && hardware.vram_free_mb != null ? total - hardware.vram_free_mb : null);
  const percent = total && used != null ? (used / total) * 100 : null;
  const installed = models.installed.length;
  return (
    <Card title={t("hardware.title")} className="dk-hardware">
      <div className="dk-hardware__head">
        <Cpu size={20} aria-hidden="true" />
        <div>
          <strong>{hardware.gpu ?? t("hardware.noGpu")}</strong>
          <span className="dk-muted">
            {hardware.cpu} · {t("hardware.cores", { count: hardware.cores })}
          </span>
        </div>
      </div>
      {total ? (
        <div className="dk-vram">
          <div className="dk-vram__label">
            <span>{t("hardware.vram")}</span>
            <span>
              {formatNumber((used ?? 0) / 1024, i18n.language)} /{" "}
              {formatNumber(total / 1024, i18n.language)} GB
            </span>
          </div>
          <ProgressBar value={percent} label={t("hardware.vram")} />
        </div>
      ) : null}
      <div className="dk-badges">
        <Badge tone="info">{t("hardware.backend", { name: hardware.backend.toUpperCase() })}</Badge>
        <Badge>{t("hardware.profile", { name: hardware.profile })}</Badge>
        <Badge tone={models.missing.length === 0 ? "success" : "warning"}>
          {t("hardware.models", { installed, total: models.needed.length })}
        </Badge>
      </div>
      {live ? (
        <div className="dk-live" aria-label={t("hardware.live")}>
          {live.temperature_c != null ? (
            <span>
              <Thermometer size={14} aria-hidden="true" /> {Math.round(live.temperature_c)} °C
            </span>
          ) : null}
          {live.power_w != null ? (
            <span>
              <Zap size={14} aria-hidden="true" /> {Math.round(live.power_w)}
              {live.power_limit_w ? ` / ${Math.round(live.power_limit_w)}` : ""} W
            </span>
          ) : null}
          {live.utilization_pct != null ? <span>{Math.round(live.utilization_pct)} %</span> : null}
        </div>
      ) : null}
    </Card>
  );
}
