import { useTranslation } from "react-i18next";

import { Card, Checkbox, Field, Input, Switch } from "@rushbase/ui";

import { ALL_STAGES, type RunOptions } from "../state/options";

interface Props {
  options: RunOptions;
  onChange: (options: RunOptions) => void;
  runnable: string[];
  disabled: boolean;
  hasTemperature: boolean;
}

/** Stages, stop time, idle-only and GPU temperature limit for the next run. */
export function RunOptionsCard({ options, onChange, runnable, disabled, hasTemperature }: Props) {
  const { t } = useTranslation();
  const chosen = options.stages.length > 0 ? options.stages : runnable;
  const toggle = (stage: string, on: boolean) => {
    const next = on ? [...chosen, stage] : chosen.filter((s) => s !== stage);
    onChange({ ...options, stages: ALL_STAGES.filter((s) => next.includes(s)) });
  };
  return (
    <Card title={t("options.title")}>
      <fieldset className="dk-stages" disabled={disabled}>
        <legend>{t("options.stages")}</legend>
        {ALL_STAGES.map((stage) => (
          <Checkbox
            key={stage}
            checked={chosen.includes(stage)}
            disabled={disabled || !runnable.includes(stage)}
            onCheckedChange={(on) => toggle(stage, on)}
            label={t(`stage.${stage}`)}
          />
        ))}
      </fieldset>
      <div className="dk-option-grid">
        <Field label={t("options.until")} hint={t("options.untilHint")}>
          {(control) => (
            <Input
              {...control}
              type="time"
              value={options.until}
              disabled={disabled}
              onChange={(e) => onChange({ ...options, until: e.target.value })}
            />
          )}
        </Field>
        <Field
          label={t("options.maxTemp")}
          hint={hasTemperature ? t("options.maxTempHint") : t("options.maxTempUnavailable")}
        >
          {(control) => (
            <Input
              {...control}
              type="number"
              min={0}
              max={95}
              inputMode="numeric"
              value={options.maxTemp || ""}
              placeholder="0"
              disabled={disabled || !hasTemperature}
              onChange={(e) => onChange({ ...options, maxTemp: Number(e.target.value) || 0 })}
            />
          )}
        </Field>
      </div>
      <Switch
        checked={options.idleOnly}
        disabled={disabled}
        onCheckedChange={(idleOnly) => onChange({ ...options, idleOnly })}
        label={t("options.idleOnly", { minutes: options.idleMinutes })}
      />
    </Card>
  );
}
