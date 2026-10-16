import { Button, EmptyState, StatusChip, Textarea, useToast } from "@rushbase/ui";
import { Pencil, ScanSearch, Sparkles } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useEditAsset } from "../../api/aiQueries";
import type { AssetDetail } from "../../api/contracts";
import { timecode } from "../../lib/ai";

type Lang = "de" | "en";

const ATTRIBUTES = [
  ["shot_size", "ai.shotSize"],
  ["camera_angle", "ai.cameraAngle"],
  ["interior_exterior", "ai.interiorExterior"],
  ["time_of_day", "ai.timeOfDay"],
  ["person_identifiable", "ai.tri"],
] as const;

function Attributes({
  values,
  people,
}: {
  values: Record<string, string | null>;
  people: number | null;
}) {
  const { t } = useTranslation();
  const rows = ATTRIBUTES.filter(([key]) => values[key] && values[key] !== "unknown");
  if (rows.length === 0 && people === null) return null;
  return (
    <dl className="ai-attributes">
      {rows.map(([key, group]) => (
        <div key={key}>
          <dt>{t(`ai.field.${key}`)}</dt>
          <dd>{t(`${group}.${values[key]}`, { defaultValue: String(values[key]) })}</dd>
        </div>
      ))}
      {people !== null ? (
        <div>
          <dt>{t("ai.field.people_count")}</dt>
          <dd>{people}</dd>
        </div>
      ) : null}
    </dl>
  );
}

/** AI description of the asset and its shots (German/English), editable by people. */
export function AiDescription({
  detail,
  canEdit,
  onSimilarShot,
  onSeek,
}: {
  detail: AssetDetail;
  canEdit: boolean;
  onSimilarShot: (shotIdx: number) => void;
  onSeek: (ms: number) => void;
}) {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const edit = useEditAsset();
  const [lang, setLang] = useState<Lang>(i18n.language === "en" ? "en" : "de");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const ai = detail.ai;
  const long = lang === "de" ? ai.summary_long_de : ai.summary_long_en;
  const short = lang === "de" ? ai.summary_short_de : ai.summary_short_en;
  const shots = detail.shots.filter(
    (s) => s.ai?.description_short_de || s.ai?.description_short_en,
  );

  if (!short && !long && shots.length === 0) {
    return (
      <EmptyState
        compact
        icon={<Sparkles size={24} />}
        title={t("ai.noDescriptionTitle")}
        description={t("ai.noDescriptionText")}
      />
    );
  }

  const save = () => {
    const body = lang === "de" ? { summary_long_de: draft } : { summary_long_en: draft };
    edit.mutate(
      { id: detail.id, ...body },
      {
        onSuccess: () => setEditing(false),
        onError: () => toast.push({ title: t("common.error"), tone: "error" }),
      },
    );
  };

  return (
    <div className="ai-description">
      <div className="ai-description__head">
        <div className="segmented segmented--text" role="group" aria-label={t("ai.language")}>
          {(["de", "en"] as const).map((key) => (
            <button
              key={key}
              type="button"
              className="segmented__btn"
              aria-pressed={lang === key}
              onClick={() => setLang(key)}
            >
              {key.toUpperCase()}
            </button>
          ))}
        </div>
        {ai.human_fields.some((f) => f.startsWith("summary")) ? (
          <StatusChip tone="filed" plain>
            {t("ai.editedByPerson")}
          </StatusChip>
        ) : null}
      </div>
      {short ? <p className="ai-description__short">{short}</p> : null}
      {editing ? (
        <div className="ai-description__edit">
          <Textarea
            aria-label={t("ai.editDescription")}
            value={draft}
            rows={6}
            onChange={(event) => setDraft(event.target.value)}
          />
          <div className="ai-description__actions">
            <Button size="sm" onClick={save} loading={edit.isPending}>
              {t("common.save")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      ) : (
        <>
          {long ? <p className="ai-description__long">{long}</p> : null}
          {canEdit ? (
            <Button
              size="sm"
              variant="ghost"
              icon={<Pencil size={14} />}
              onClick={() => {
                setDraft(long ?? "");
                setEditing(true);
              }}
            >
              {t("ai.editDescription")}
            </Button>
          ) : null}
        </>
      )}
      <Attributes
        values={{
          shot_size: ai.shot_size,
          camera_angle: ai.camera_angle,
          interior_exterior: ai.interior_exterior,
          time_of_day: ai.time_of_day,
          person_identifiable: ai.person_identifiable,
        }}
        people={ai.people_count}
      />
      {shots.length > 0 ? (
        <section aria-label={t("ai.shotDescriptions")}>
          <h3 className="ai-description__subtitle">{t("ai.shotDescriptions")}</h3>
          <ol className="ai-shots">
            {shots.map((shot) => {
              const frame =
                shot.keyframes.find((k) => k.position === "middle") ?? shot.keyframes[0];
              const text =
                lang === "de" ? shot.ai?.description_long_de : shot.ai?.description_long_en;
              const brief =
                lang === "de" ? shot.ai?.description_short_de : shot.ai?.description_short_en;
              return (
                <li key={shot.idx} className="ai-shots__item">
                  {frame?.url ? (
                    <button
                      type="button"
                      className="ai-shots__thumb"
                      onClick={() => onSeek(shot.start_ms)}
                      aria-label={t("asset.jumpTo", { time: timecode(shot.start_ms) })}
                    >
                      <img src={frame.url} alt="" loading="lazy" width={128} height={72} />
                    </button>
                  ) : null}
                  <div>
                    <strong>
                      {t("asset.shotLabel", {
                        n: shot.idx + 1,
                        start: timecode(shot.start_ms),
                        end: timecode(shot.end_ms),
                      })}
                    </strong>
                    <p>{brief}</p>
                    {text && text !== brief ? <p className="muted">{text}</p> : null}
                    {shot.ai ? (
                      <Attributes
                        values={{
                          shot_size: shot.ai.shot_size,
                          camera_angle: shot.ai.camera_angle,
                          interior_exterior: shot.ai.interior_exterior,
                          time_of_day: shot.ai.time_of_day,
                          person_identifiable: shot.ai.person_identifiable,
                        }}
                        people={shot.ai.people_count}
                      />
                    ) : null}
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<ScanSearch size={14} />}
                      onClick={() => onSimilarShot(shot.idx)}
                    >
                      {t("ai.similarScenes")}
                    </Button>
                  </div>
                </li>
              );
            })}
          </ol>
        </section>
      ) : null}
    </div>
  );
}
