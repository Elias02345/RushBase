import { Button, Dialog, Field, Input, Select } from "@rushbase/ui";
import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useBulkEdit } from "../../api/aiQueries";

type Choice = "keep" | "yes" | "no" | "unknown";
const CHOICES: Choice[] = ["keep", "yes", "no", "unknown"];

/** Bulk edit of person identifiable, consent and proof reference for the selected assets. */
export function BulkPrivacy({ ids, onDone }: { ids: string[]; onDone: () => void }) {
  const { t } = useTranslation();
  const bulk = useBulkEdit();
  const [open, setOpen] = useState(false);
  const [person, setPerson] = useState<Choice>("keep");
  const [consent, setConsent] = useState<Choice>("keep");
  const [reference, setReference] = useState("");
  const options = CHOICES.map((v) => ({ value: v, label: t(`privacy.consentChoice.${v}`) }));
  return (
    <>
      <Button
        size="sm"
        variant="secondary"
        icon={<ShieldCheck size={14} />}
        onClick={() => setOpen(true)}
      >
        {t("privacy.bulkButton")}
      </Button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title={t("privacy.bulkTitle", { count: ids.length })}
        closeLabel={t("common.close")}
        footer={
          <Button
            loading={bulk.isPending}
            onClick={() =>
              bulk.mutate(
                {
                  asset_ids: ids,
                  ...(person !== "keep" ? { person_identifiable: person } : {}),
                  ...(consent !== "keep" ? { consent } : {}),
                  ...(reference.trim() ? { consent_ref: reference.trim() } : {}),
                },
                {
                  onSuccess: () => {
                    setOpen(false);
                    onDone();
                  },
                },
              )
            }
          >
            {t("common.save")}
          </Button>
        }
      >
        <div className="form-grid">
          <Field label={t("ai.field.person_identifiable")}>
            {({ id }) => (
              <Select
                id={id}
                value={person}
                onValueChange={(v) => setPerson(v as Choice)}
                options={options}
              />
            )}
          </Field>
          <Field label={t("privacy.consent")}>
            {({ id }) => (
              <Select
                id={id}
                value={consent}
                onValueChange={(v) => setConsent(v as Choice)}
                options={options}
              />
            )}
          </Field>
          <Field label={t("privacy.consentRef")}>
            {(c) => (
              <Input
                {...c}
                value={reference}
                maxLength={300}
                onChange={(e) => setReference(e.target.value)}
              />
            )}
          </Field>
        </div>
      </Dialog>
    </>
  );
}
