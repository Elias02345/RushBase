import { Button, EmptyState, Skeleton, useToast } from "@rushbase/ui";
import { ListChecks, SkipForward } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useGoldenFields, useGoldenItems, useGoldenStats, useSetLabel } from "../api/aiQueries";
import type { GoldenField, GoldenTarget } from "../api/contracts";
import { timecode } from "../lib/ai";

const CORE_GROUPS: Record<string, string> = {
  shot_size: "ai.shotSize",
  camera_angle: "ai.cameraAngle",
  interior_exterior: "ai.interiorExterior",
  time_of_day: "ai.timeOfDay",
  person_identifiable: "ai.tri",
};

function valueText(
  t: (key: string, options?: Record<string, unknown>) => string,
  field: GoldenField,
  value: string,
): string {
  const group = CORE_GROUPS[field.key];
  return group ? t(`${group}.${value}`, { defaultValue: value }) : value;
}

/** Golden set labelling: keyframes plus one row per field, driven by the keyboard. */
export function GoldenPage() {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const [skip, setSkip] = useState(0);
  const items = useGoldenItems({ unlabeled: true, offset: skip });
  const fieldsQuery = useGoldenFields();
  const stats = useGoldenStats();
  const setLabel = useSetLabel();
  const fields = useMemo(() => fieldsQuery.data ?? [], [fieldsQuery.data]);
  const item = items.data?.items[0];
  const [targetIdx, setTargetIdx] = useState(0);
  const [fieldIdx, setFieldIdx] = useState(0);
  const [local, setLocal] = useState<Record<string, string>>({});
  const targets: GoldenTarget[] = item?.targets ?? [];
  const target = targets[Math.min(targetIdx, Math.max(0, targets.length - 1))];
  const field = fields[fieldIdx];
  const key = (shotId: string | null, name: string) => `${item?.asset.id}|${shotId ?? ""}|${name}`;
  const valueOf = (tg: GoldenTarget, name: string) =>
    local[key(tg.shot_id, name)] ?? tg.labels[name];

  useEffect(() => {
    setTargetIdx(0);
    setFieldIdx(0);
  }, [item?.asset.id]);

  const choose = useCallback(
    (value: string) => {
      if (!item || !target || !field) return;
      setLocal((current) => ({
        ...current,
        [`${item.asset.id}|${target.shot_id ?? ""}|${field.key}`]: value,
      }));
      setLabel.mutate(
        { asset_id: item.asset.id, shot_id: target.shot_id, field: field.key, expected: value },
        { onError: () => toast.push({ title: t("common.error"), tone: "error" }) },
      );
      setFieldIdx((i) => Math.min(fields.length - 1, i + 1));
    },
    [item, target, field, fields.length, setLabel, t, toast],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const el = event.target as HTMLElement | null;
      if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (/^[1-9]$/.test(event.key) && field) {
        const value = field.values[Number(event.key) - 1];
        if (value) choose(value);
      } else if (event.key === "Enter" && target && field) {
        const suggestion = (target.ai as Record<string, string | null>)[field.key];
        if (suggestion && field.values.includes(suggestion)) choose(suggestion);
      } else if (event.key === "ArrowDown") {
        setFieldIdx((i) => Math.min(fields.length - 1, i + 1));
      } else if (event.key === "ArrowUp") {
        setFieldIdx((i) => Math.max(0, i - 1));
      } else if (event.key === "ArrowRight") {
        setTargetIdx((i) => Math.min(targets.length - 1, i + 1));
      } else if (event.key === "ArrowLeft") {
        setTargetIdx((i) => Math.max(0, i - 1));
      } else if (event.key === "n") {
        setSkip((s) => s + 1);
      } else {
        return;
      }
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [choose, field, fields.length, target, targets.length]);

  return (
    <div className="page golden-page">
      <header className="page__head">
        <div>
          <h1 className="page__title">{t("golden.title")}</h1>
          <p className="page__lead">
            {t("golden.lead", {
              labels: stats.data?.labels ?? 0,
              assets: stats.data?.assets ?? 0,
              corrections: stats.data?.corrections ?? 0,
            })}
          </p>
        </div>
        <Button
          variant="secondary"
          icon={<SkipForward size={16} />}
          onClick={() => setSkip((s) => s + 1)}
        >
          {t("golden.skip")} <kbd>N</kbd>
        </Button>
      </header>
      {items.isLoading || fieldsQuery.isLoading ? (
        <Skeleton height={360} radius={16} />
      ) : !item || !target ? (
        <EmptyState
          icon={<ListChecks size={28} />}
          title={t("golden.emptyTitle")}
          description={t("golden.emptyText")}
          action={
            skip > 0 ? <Button onClick={() => setSkip(0)}>{t("golden.restart")}</Button> : undefined
          }
        />
      ) : (
        <div className="golden-layout">
          <section className="golden-frames" aria-label={t("golden.frames")}>
            <h2>{item.asset.title ?? item.asset.filename}</h2>
            {targets.length > 1 ? (
              <div className="golden-targets" role="group" aria-label={t("golden.shots")}>
                {targets.map((tg, i) => (
                  <button
                    key={tg.shot_id ?? "asset"}
                    type="button"
                    aria-pressed={tg === target}
                    className="golden-targets__btn"
                    onClick={() => setTargetIdx(i)}
                  >
                    {tg.start_ms !== null ? timecode(tg.start_ms) : i + 1}
                  </button>
                ))}
              </div>
            ) : null}
            <div className="golden-frames__row">
              {target.keyframe_urls.map((url) => (
                <img key={url} src={url} alt="" loading="lazy" />
              ))}
            </div>
          </section>
          <section className="golden-fields" aria-label={t("golden.fields")}>
            {fields.map((f, i) => {
              const chosen = valueOf(target, f.key);
              const suggestion = (target.ai as Record<string, string | null>)[f.key];
              return (
                <div
                  key={f.key}
                  className="golden-field"
                  aria-current={i === fieldIdx || undefined}
                  onClick={() => setFieldIdx(i)}
                >
                  <h3>{i18n.language === "en" ? f.label_en : f.label_de}</h3>
                  <div className="golden-field__values" role="group" aria-label={f.label_de}>
                    {f.values.map((value, n) => (
                      <button
                        key={value}
                        type="button"
                        aria-pressed={chosen === value}
                        className={`golden-value${suggestion === value ? " golden-value--ai" : ""}`}
                        onClick={() => {
                          setFieldIdx(i);
                          if (item) {
                            setLocal((c) => ({ ...c, [key(target.shot_id, f.key)]: value }));
                            setLabel.mutate({
                              asset_id: item.asset.id,
                              shot_id: target.shot_id,
                              field: f.key,
                              expected: value,
                            });
                          }
                        }}
                      >
                        {i === fieldIdx && n < 9 ? <kbd>{n + 1}</kbd> : null}
                        {valueText(t, f, value)}
                        {suggestion === value ? (
                          <span className="golden-value__ai">{t("golden.aiGuess")}</span>
                        ) : null}
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}
            <p className="muted">{t("golden.keys")}</p>
          </section>
        </div>
      )}
    </div>
  );
}
