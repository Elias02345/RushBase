import { Button, EmptyState, IconButton, Select, StatusChip, Switch, useToast } from "@rushbase/ui";
import { Check, Lock, Plus, Tags, X } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useEditAsset, useTaxonomy } from "../../api/aiQueries";
import type { AssetDetail, TagInfo } from "../../api/contracts";
import { groupTags, percent, tagLabel } from "../../lib/ai";

const SOURCE_TONE = { human: "filed", claude: "processing", ai: "neutral" } as const;

function TagChip({
  tag,
  language,
  editable,
  busy,
  onRemove,
  onConfirm,
}: {
  tag: TagInfo;
  language: string;
  editable: boolean;
  busy: boolean;
  onRemove: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  const label = tagLabel(tag, language);
  const source = tag.source as keyof typeof SOURCE_TONE;
  return (
    <li className="tag-chip">
      <span className="tag-chip__name">{label}</span>
      <StatusChip
        tone={SOURCE_TONE[source] ?? "neutral"}
        plain
        title={t(`tags.sourceHint.${source}`)}
      >
        {t(`tags.source.${source}`)}
        {tag.confidence !== null && source !== "human" ? ` ${percent(tag.confidence)}` : ""}
      </StatusChip>
      {tag.shot_idx !== null ? (
        <span className="muted">{t("tags.shot", { n: tag.shot_idx + 1 })}</span>
      ) : null}
      {editable ? (
        <span className="tag-chip__actions">
          {source !== "human" ? (
            <IconButton
              label={t("tags.confirm", { tag: label })}
              icon={<Check size={14} />}
              size="sm"
              disabled={busy}
              onClick={onConfirm}
            />
          ) : null}
          <IconButton
            label={t("tags.remove", { tag: label })}
            icon={<X size={14} />}
            size="sm"
            disabled={busy}
            onClick={onRemove}
          />
        </span>
      ) : null}
    </li>
  );
}

/** Tags grouped by taxonomy field with provenance; editors confirm, remove and add inline. */
export function TagsPanel({ detail, canEdit }: { detail: AssetDetail; canEdit: boolean }) {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const taxonomy = useTaxonomy();
  const edit = useEditAsset();
  const [fieldKey, setFieldKey] = useState("");
  const [value, setValue] = useState("");
  const groups = groupTags(detail.ai.tags);
  const fields = useMemo(
    () =>
      (taxonomy.data?.fields ?? []).filter((f) => f.values.some((v) => v.status === "approved")),
    [taxonomy.data],
  );
  const selected = fields.find((f) => f.key === fieldKey);
  const run = (body: Parameters<typeof edit.mutate>[0], done?: () => void) =>
    edit.mutate(body, {
      onSuccess: done,
      onError: () => toast.push({ title: t("common.error"), tone: "error" }),
    });
  const language = i18n.language;

  return (
    <div className="tags-panel">
      <div className="tags-panel__head">
        <Switch
          checked={detail.ai.locked}
          onCheckedChange={(locked) => run({ id: detail.id, locked })}
          disabled={!canEdit || edit.isPending}
          label={
            <span className="tags-panel__lock">
              <Lock size={14} aria-hidden="true" />
              {t("tags.lock")}
            </span>
          }
        />
        <p className="muted">{t("tags.lockHint")}</p>
      </div>
      {groups.length === 0 ? (
        <EmptyState
          compact
          icon={<Tags size={24} />}
          title={t("asset.tagsTitle")}
          description={t("asset.tagsText")}
        />
      ) : (
        groups.map((group) => (
          <section
            key={group.key}
            className="tag-group"
            aria-label={language === "en" ? group.labelEn : group.labelDe}
          >
            <h3>{language === "en" ? group.labelEn : group.labelDe}</h3>
            <ul>
              {group.tags.map((tag) => (
                <TagChip
                  key={tag.id}
                  tag={tag}
                  language={language}
                  editable={canEdit}
                  busy={edit.isPending}
                  onRemove={() =>
                    run({
                      id: detail.id,
                      remove_tags: [{ field: tag.field_key, value: tag.value }],
                    })
                  }
                  onConfirm={() =>
                    run({ id: detail.id, add_tags: [{ field: tag.field_key, value: tag.value }] })
                  }
                />
              ))}
            </ul>
          </section>
        ))
      )}
      {canEdit && fields.length > 0 ? (
        <form
          className="tags-panel__add"
          onSubmit={(event) => {
            event.preventDefault();
            if (!fieldKey || !value) return;
            run({ id: detail.id, add_tags: [{ field: fieldKey, value }] }, () => setValue(""));
          }}
        >
          <Select
            aria-label={t("tags.field")}
            placeholder={t("tags.field")}
            value={fieldKey}
            onValueChange={(next) => {
              setFieldKey(next);
              setValue("");
            }}
            options={fields.map((f) => ({
              value: f.key,
              label: language === "en" ? f.label_en : f.label_de,
            }))}
          />
          <Select
            aria-label={t("tags.value")}
            placeholder={t("tags.value")}
            value={value}
            disabled={!selected}
            onValueChange={setValue}
            options={(selected?.values ?? [])
              .filter((v) => v.status === "approved")
              .map((v) => ({
                value: v.value,
                label: (language === "en" ? v.label_en : v.label_de) ?? v.value,
              }))}
          />
          <Button
            type="submit"
            size="sm"
            icon={<Plus size={14} />}
            disabled={!value || edit.isPending}
          >
            {t("tags.add")}
          </Button>
        </form>
      ) : null}
    </div>
  );
}
