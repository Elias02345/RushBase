import {
  Button,
  Card,
  Field,
  Input,
  Select,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  useToast,
} from "@rushbase/ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { useBatches } from "../api/queries";
import { usePrivacyLock, type LockResult } from "../api/safetyQueries";

type Mode = "batch" | "person";
const CONSENT = ["keep", "yes", "no", "unknown"] as const;

/** Find and lock all assets of a batch or of a person (optionally setting consent). */
export function PrivacyPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const batches = useBatches();
  const lock = usePrivacyLock();
  const [mode, setMode] = useState<Mode>("person");
  const [batch, setBatch] = useState("");
  const [person, setPerson] = useState("");
  const [consent, setConsent] = useState<(typeof CONSENT)[number]>("keep");
  const [reference, setReference] = useState("");
  const [found, setFound] = useState<LockResult | null>(null);

  const target = mode === "batch" ? { batch_id: batch } : { person: person.trim() };
  const ready = mode === "batch" ? Boolean(batch) : person.trim().length > 0;
  const body = (dry_run: boolean) => ({
    ...target,
    dry_run,
    ...(consent !== "keep" ? { consent } : {}),
    ...(reference.trim() ? { consent_ref: reference.trim() } : {}),
  });

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">{t("privacy.title")}</h1>
          <p className="page__lead">{t("privacy.lead")}</p>
        </div>
      </header>
      <Card>
        <Tabs
          value={mode}
          onValueChange={(value) => {
            setMode(value as Mode);
            setFound(null);
          }}
        >
          <TabsList aria-label={t("privacy.selectBy")}>
            <TabsTrigger value="person">{t("privacy.byPerson")}</TabsTrigger>
            <TabsTrigger value="batch">{t("privacy.byBatch")}</TabsTrigger>
          </TabsList>
          <TabsContent value="person">
            <p className="muted">{t("privacy.personHint")}</p>
          </TabsContent>
          <TabsContent value="batch">
            <p className="muted">{t("privacy.batchHint")}</p>
          </TabsContent>
        </Tabs>
        <form
          className="form-grid"
          onSubmit={(event) => {
            event.preventDefault();
            lock.mutate(body(true), { onSuccess: setFound });
          }}
        >
          {mode === "person" ? (
            <Field label={t("privacy.person")}>
              {(c) => (
                <Input
                  {...c}
                  value={person}
                  maxLength={100}
                  onChange={(e) => {
                    setPerson(e.target.value);
                    setFound(null);
                  }}
                />
              )}
            </Field>
          ) : (
            <Field label={t("privacy.batch")}>
              {({ id }) => (
                <Select
                  id={id}
                  value={batch}
                  placeholder={t("migration.batchPlaceholder")}
                  onValueChange={(value) => {
                    setBatch(value);
                    setFound(null);
                  }}
                  options={(batches.data ?? []).map((b) => ({ value: b.id, label: b.label }))}
                />
              )}
            </Field>
          )}
          <Field label={t("privacy.setConsent")}>
            {({ id }) => (
              <Select
                id={id}
                value={consent}
                onValueChange={(value) => setConsent(value as (typeof CONSENT)[number])}
                options={CONSENT.map((v) => ({ value: v, label: t(`privacy.consentChoice.${v}`) }))}
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
          <div>
            <Button type="submit" variant="secondary" disabled={!ready} loading={lock.isPending}>
              {t("privacy.find")}
            </Button>
          </div>
        </form>
        {lock.error instanceof ApiError ? (
          <p className="form-error" role="alert">
            {lock.error.message}
          </p>
        ) : null}
        {found ? (
          <div>
            <p>{t("privacy.found", { count: found.matched })}</p>
            <Button
              disabled={found.matched === 0}
              loading={lock.isPending}
              onClick={() =>
                lock.mutate(body(false), {
                  onSuccess: (done) => {
                    setFound(null);
                    toast.push({
                      title: t("privacy.lockedDone", { count: done.updated }),
                      tone: "success",
                    });
                  },
                })
              }
            >
              {t("privacy.lockAll", { count: found.matched })}
            </Button>
          </div>
        ) : null}
      </Card>
      <p className="muted">{t("privacy.faceNote")}</p>
    </div>
  );
}
