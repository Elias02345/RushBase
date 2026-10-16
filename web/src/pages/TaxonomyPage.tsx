import {
  Button,
  Dialog,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
  StatusChip,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  useToast,
} from "@rushbase/ui";
import { Check, GitMerge, Pencil, Plus, Tags } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useProposals, useTaxonomy, useTaxonomyActions } from "../api/aiQueries";
import type { Proposal, TaxonomyField, TaxonomyValue } from "../api/contracts";
import { hasRole, useMe } from "../api/queries";

type Mode = { kind: "rename" | "synonym" | "merge"; value: TaxonomyValue | Proposal };

const TONE = { approved: "filed", proposed: "review", deprecated: "neutral" } as const;

function ValueDialog({
  mode,
  siblings,
  onClose,
}: {
  mode: Mode | null;
  siblings: TaxonomyValue[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const actions = useTaxonomyActions();
  const [text, setText] = useState("");
  const [target, setTarget] = useState("");
  if (!mode) return null;
  const done = {
    onSuccess: onClose,
    onError: () => toast.push({ title: t("common.error"), tone: "error" }),
  };
  const submit = () => {
    if (mode.kind === "rename") actions.rename.mutate({ id: mode.value.id, value: text }, done);
    else if (mode.kind === "synonym")
      actions.synonym.mutate({ id: mode.value.id, word: text }, done);
    else actions.merge.mutate({ id: mode.value.id, target }, done);
  };
  const candidates = siblings.filter(
    (s) =>
      s.field_id === mode.value.field_id && s.id !== mode.value.id && s.status !== "deprecated",
  );
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={t(`taxonomy.dialog.${mode.kind}`, { value: mode.value.value })}
      closeLabel={t("common.close")}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button onClick={submit} disabled={mode.kind === "merge" ? !target : !text.trim()}>
            {t("taxonomy.dialog.confirm")}
          </Button>
        </>
      }
    >
      {mode.kind === "merge" ? (
        <Field label={t("taxonomy.mergeInto")}>
          {({ id }) => (
            <Select
              id={id}
              value={target}
              onValueChange={setTarget}
              placeholder={t("taxonomy.chooseTarget")}
              options={candidates.map((c) => ({ value: c.id, label: c.value }))}
            />
          )}
        </Field>
      ) : (
        <Field label={t(mode.kind === "rename" ? "taxonomy.newName" : "taxonomy.synonym")}>
          {(control) => (
            <Input
              {...control}
              value={text}
              onChange={(event) => setText(event.target.value)}
              autoFocus
            />
          )}
        </Field>
      )}
    </Dialog>
  );
}

function FieldCard({
  field,
  canAdmin,
  onMode,
}: {
  field: TaxonomyField;
  canAdmin: boolean;
  onMode: (mode: Mode) => void;
}) {
  const { t, i18n } = useTranslation();
  const actions = useTaxonomyActions();
  const [draft, setDraft] = useState("");
  const label = i18n.language === "en" ? field.label_en : field.label_de;
  return (
    <section className="tax-field" aria-label={label}>
      <header>
        <h3>{label}</h3>
        <span className="muted">
          {field.key} · {t(`taxonomy.kind.${field.kind}`)}
        </span>
      </header>
      {field.values.length === 0 ? (
        <p className="muted">{t("taxonomy.noValues")}</p>
      ) : (
        <ul className="tax-values">
          {field.values.map((value) => (
            <li key={value.id}>
              <div>
                <strong>{value.value}</strong>{" "}
                <StatusChip tone={TONE[value.status as keyof typeof TONE] ?? "neutral"} plain>
                  {t(`taxonomy.status.${value.status}`)}
                </StatusChip>{" "}
                <span className="muted">{t("taxonomy.assets", { count: value.asset_count })}</span>
                {value.synonyms.length > 0 ? (
                  <span className="muted"> · {value.synonyms.join(", ")}</span>
                ) : null}
              </div>
              {canAdmin ? (
                <div className="tax-values__actions">
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={<Pencil size={14} />}
                    onClick={() => onMode({ kind: "rename", value })}
                  >
                    {t("taxonomy.rename")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => onMode({ kind: "synonym", value })}
                  >
                    {t("taxonomy.addSynonym")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={<GitMerge size={14} />}
                    onClick={() => onMode({ kind: "merge", value })}
                  >
                    {t("taxonomy.merge")}
                  </Button>
                  {value.status === "approved" ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => actions.deprecate.mutate(value.id)}
                    >
                      {t("taxonomy.deprecate")}
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => actions.approve.mutate(value.id)}
                    >
                      {t("taxonomy.approve")}
                    </Button>
                  )}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      <form
        className="tax-field__add"
        onSubmit={(event) => {
          event.preventDefault();
          if (!draft.trim()) return;
          actions.createValue.mutate(
            { fieldId: field.id, value: draft.trim() },
            { onSuccess: () => setDraft("") },
          );
        }}
      >
        <Input
          aria-label={t("taxonomy.newValue", { field: label })}
          placeholder={t("taxonomy.newValue", { field: label })}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <Button type="submit" size="sm" icon={<Plus size={14} />} disabled={!draft.trim()}>
          {t("taxonomy.add")}
        </Button>
      </form>
    </section>
  );
}

function Proposals({ canAdmin, onMode }: { canAdmin: boolean; onMode: (mode: Mode) => void }) {
  const { t, i18n } = useTranslation();
  const proposals = useProposals(true);
  const actions = useTaxonomyActions();
  if (proposals.isLoading) return <Skeleton height={200} radius={16} />;
  const items = proposals.data ?? [];
  if (items.length === 0) {
    return (
      <EmptyState
        compact
        icon={<Tags size={24} />}
        title={t("taxonomy.noProposalsTitle")}
        description={t("taxonomy.noProposalsText")}
      />
    );
  }
  return (
    <ul className="tax-proposals" aria-label={t("taxonomy.proposals")}>
      {items.map((proposal) => (
        <li key={proposal.id}>
          <div className="tax-proposals__main">
            <strong>{proposal.value}</strong>
            <span className="muted">
              {i18n.language === "en" ? proposal.field_label_en : proposal.field_label_de} ·{" "}
              {t("taxonomy.proposedTimes", { count: proposal.proposal_count })}
            </span>
            <div className="tax-proposals__examples">
              {proposal.examples.map((example) =>
                example.poster_url ? (
                  <img
                    key={example.asset_id}
                    src={example.poster_url}
                    alt={example.filename}
                    width={96}
                    height={54}
                    loading="lazy"
                  />
                ) : (
                  <span key={example.asset_id} className="muted">
                    {example.filename}
                  </span>
                ),
              )}
            </div>
          </div>
          <div className="tax-values__actions">
            <Button
              size="sm"
              icon={<Check size={14} />}
              onClick={() => actions.approve.mutate(proposal.id)}
            >
              {t("taxonomy.approve")}
            </Button>
            {canAdmin ? (
              <>
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<GitMerge size={14} />}
                  onClick={() => onMode({ kind: "merge", value: proposal })}
                >
                  {t("taxonomy.merge")}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  icon={<Pencil size={14} />}
                  onClick={() => onMode({ kind: "rename", value: proposal })}
                >
                  {t("taxonomy.rename")}
                </Button>
              </>
            ) : null}
          </div>
        </li>
      ))}
    </ul>
  );
}

/** Taxonomy administration: fields, values and the proposals the AI collected. */
export function TaxonomyPage() {
  const { t } = useTranslation();
  const me = useMe();
  const taxonomy = useTaxonomy();
  const proposals = useProposals(true);
  const actions = useTaxonomyActions();
  const canAdmin = hasRole(me.data?.role, "admin");
  const [mode, setMode] = useState<Mode | null>(null);
  const [key, setKey] = useState("");
  const [labelDe, setLabelDe] = useState("");
  const [labelEn, setLabelEn] = useState("");
  const all = taxonomy.data?.fields.flatMap((f) => f.values) ?? [];
  const mergeSiblings = [...all];

  return (
    <div className="page taxonomy-page">
      <header className="page__head">
        <div>
          <h1 className="page__title">{t("taxonomy.title")}</h1>
          <p className="page__lead">
            {t("taxonomy.lead", { version: taxonomy.data?.version ?? 1 })}
          </p>
        </div>
      </header>
      <Tabs defaultValue="proposals">
        <TabsList aria-label={t("taxonomy.sections")}>
          <TabsTrigger value="proposals">
            {t("taxonomy.proposals")} ({proposals.data?.length ?? 0})
          </TabsTrigger>
          <TabsTrigger value="fields">{t("taxonomy.fields")}</TabsTrigger>
        </TabsList>
        <TabsContent value="proposals">
          <Proposals canAdmin={canAdmin} onMode={setMode} />
        </TabsContent>
        <TabsContent value="fields">
          {taxonomy.isLoading ? (
            <Skeleton height={240} radius={16} />
          ) : (
            <div className="tax-fields">
              {taxonomy.data?.fields.map((field) => (
                <FieldCard key={field.id} field={field} canAdmin={canAdmin} onMode={setMode} />
              ))}
              {canAdmin ? (
                <form
                  className="tax-newfield"
                  onSubmit={(event) => {
                    event.preventDefault();
                    actions.createField.mutate(
                      { key, label_de: labelDe, label_en: labelEn || labelDe, kind: "multi" },
                      {
                        onSuccess: () => {
                          setKey("");
                          setLabelDe("");
                          setLabelEn("");
                        },
                      },
                    );
                  }}
                >
                  <h3>{t("taxonomy.newField")}</h3>
                  <Field label={t("taxonomy.fieldKey")} hint={t("taxonomy.fieldKeyHint")}>
                    {(control) => (
                      <Input
                        {...control}
                        value={key}
                        onChange={(e) => setKey(e.target.value)}
                        pattern="[a-z][a-z0-9_]*"
                        required
                      />
                    )}
                  </Field>
                  <Field label={t("taxonomy.labelDe")}>
                    {(control) => (
                      <Input
                        {...control}
                        value={labelDe}
                        onChange={(e) => setLabelDe(e.target.value)}
                        required
                      />
                    )}
                  </Field>
                  <Field label={t("taxonomy.labelEn")}>
                    {(control) => (
                      <Input
                        {...control}
                        value={labelEn}
                        onChange={(e) => setLabelEn(e.target.value)}
                      />
                    )}
                  </Field>
                  <Button type="submit" icon={<Plus size={16} />}>
                    {t("taxonomy.createField")}
                  </Button>
                </form>
              ) : null}
            </div>
          )}
        </TabsContent>
      </Tabs>
      <ValueDialog mode={mode} siblings={mergeSiblings} onClose={() => setMode(null)} />
    </div>
  );
}
