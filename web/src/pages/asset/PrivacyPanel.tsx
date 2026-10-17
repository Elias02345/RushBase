import { Button, Field, Input, Select, Switch, useToast } from "@rushbase/ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useEditAsset } from "../../api/aiQueries";
import type { AssetDetail } from "../../api/contracts";

type Tri = "yes" | "no" | "unknown";
const TRI: Tri[] = ["unknown", "yes", "no"];

/** Person identifiable, consent (yes/no/unknown plus proof reference) and the lock. */
export function PrivacyPanel({ detail, canEdit }: { detail: AssetDetail; canEdit: boolean }) {
  const { t } = useTranslation();
  const toast = useToast();
  const edit = useEditAsset();
  const ai = detail.ai;
  const [person, setPerson] = useState(ai.person_identifiable as Tri);
  const [consent, setConsent] = useState(ai.consent as Tri);
  const [reference, setReference] = useState(ai.consent_ref ?? "");
  const [locked, setLocked] = useState(ai.locked);
  const options = (group: string) => TRI.map((v) => ({ value: v, label: t(`${group}.${v}`) }));

  return (
    <form
      className="form-grid privacy-form"
      onSubmit={(event) => {
        event.preventDefault();
        edit.mutate(
          {
            id: detail.id,
            person_identifiable: person,
            consent,
            consent_ref: reference.trim() || null,
            locked,
          },
          {
            onSuccess: () => toast.push({ title: t("common.saved"), tone: "success" }),
            onError: () => toast.push({ title: t("common.error"), tone: "error" }),
          },
        );
      }}
    >
      <p className="muted">{t("privacy.asset.lead")}</p>
      <Field label={t("ai.field.person_identifiable")} hint={t("privacy.asset.personHint")}>
        {({ id }) => (
          <Select
            id={id}
            value={person}
            disabled={!canEdit}
            onValueChange={(v) => setPerson(v as Tri)}
            options={options("ai.tri")}
          />
        )}
      </Field>
      <Field label={t("privacy.consent")}>
        {({ id }) => (
          <Select
            id={id}
            value={consent}
            disabled={!canEdit}
            onValueChange={(v) => setConsent(v as Tri)}
            options={options("privacy.consentValue")}
          />
        )}
      </Field>
      <Field label={t("privacy.consentRef")} hint={t("privacy.consentRefHint")}>
        {(c) => (
          <Input
            {...c}
            value={reference}
            maxLength={300}
            disabled={!canEdit}
            onChange={(e) => setReference(e.target.value)}
          />
        )}
      </Field>
      <Switch
        label={t("privacy.locked")}
        checked={locked}
        disabled={!canEdit}
        onCheckedChange={setLocked}
      />
      <p className="muted">{t("privacy.lockedHint")}</p>
      {canEdit ? (
        <div>
          <Button type="submit" loading={edit.isPending}>
            {t("common.save")}
          </Button>
        </div>
      ) : null}
    </form>
  );
}
