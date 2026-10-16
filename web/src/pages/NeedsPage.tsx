import {
  Button,
  Card,
  Dialog,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
  StatusChip,
  Textarea,
  useToast,
} from "@rushbase/ui";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { Check, ClipboardList, Plus, X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { hasRole, useMe } from "../api/queries";
import type { NeedCandidate, NeedItem } from "../api/contracts";
import { useNeed, useNeedActions, useNeedLists } from "../api/mcpQueries";
import { formatDateTime } from "../lib/format";

const ITEM_TONE = { open: "review", fulfilled: "filed", dropped: "neutral" } as const;
const CANDIDATE_TONE = { candidate: "review", confirmed: "filed", rejected: "neutral" } as const;

function CreateDialog({ open, onClose }: { open: boolean; onClose: (id?: string) => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { create } = useNeedActions();
  const [title, setTitle] = useState("");
  const [purpose, setPurpose] = useState("");
  const [lines, setLines] = useState("");
  if (!open) return null;
  const submit = () => {
    const items = lines
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((description) => ({ description, count_wanted: 1 }));
    create.mutate(
      { title, purpose, items },
      {
        onSuccess: (need) => {
          setTitle("");
          setPurpose("");
          setLines("");
          onClose(need.id);
        },
        onError: () => toast.push({ title: t("common.error"), tone: "error" }),
      },
    );
  };
  return (
    <Dialog
      open
      onOpenChange={(next) => !next && onClose()}
      title={t("needs.create")}
      closeLabel={t("common.close")}
      footer={
        <>
          <Button variant="ghost" onClick={() => onClose()}>
            {t("common.cancel")}
          </Button>
          <Button onClick={submit} disabled={!title.trim()} loading={create.isPending}>
            {t("needs.createConfirm")}
          </Button>
        </>
      }
    >
      <Field label={t("needs.title")}>
        {(control) => (
          <Input
            {...control}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            autoFocus
          />
        )}
      </Field>
      <Field label={t("needs.purpose")}>
        {(control) => (
          <Input
            {...control}
            value={purpose}
            onChange={(event) => setPurpose(event.target.value)}
          />
        )}
      </Field>
      <Field label={t("needs.itemsHint")}>
        {(control) => (
          <Textarea
            {...control}
            rows={5}
            value={lines}
            onChange={(event) => setLines(event.target.value)}
          />
        )}
      </Field>
    </Dialog>
  );
}

function Candidate({
  candidate,
  itemId,
  canEdit,
}: {
  candidate: NeedCandidate;
  itemId: string;
  canEdit: boolean;
}) {
  const { t } = useTranslation();
  const { decide } = useNeedActions();
  const asset = candidate.asset;
  return (
    <li className="need-candidate">
      <Link to="/assets/$id" params={{ id: candidate.asset_id }} className="need-candidate__link">
        {asset?.poster_url ? (
          <img src={asset.poster_url} alt="" loading="lazy" className="need-candidate__thumb" />
        ) : (
          <span className="need-candidate__thumb need-candidate__thumb--empty" aria-hidden="true" />
        )}
        <span className="need-candidate__name">{asset?.filename ?? candidate.asset_id}</span>
      </Link>
      <div className="need-candidate__meta">
        <StatusChip tone={CANDIDATE_TONE[candidate.status as keyof typeof CANDIDATE_TONE]} plain>
          {t(`needs.candidateStatus.${candidate.status}`)}
        </StatusChip>
        <span className="muted">
          {t(`needs.source.${candidate.source}`)} · {candidate.score.toFixed(3)}
        </span>
      </div>
      {canEdit && candidate.status !== "confirmed" ? (
        <div className="need-candidate__actions">
          <Button
            size="sm"
            icon={<Check size={14} />}
            onClick={() =>
              decide.mutate({ item: itemId, asset: candidate.asset_id, status: "confirmed" })
            }
          >
            {t("needs.confirm")}
          </Button>
          {candidate.status !== "rejected" ? (
            <Button
              size="sm"
              variant="ghost"
              icon={<X size={14} />}
              onClick={() =>
                decide.mutate({ item: itemId, asset: candidate.asset_id, status: "rejected" })
              }
            >
              {t("needs.reject")}
            </Button>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function Item({ item, canEdit }: { item: NeedItem; canEdit: boolean }) {
  const { t } = useTranslation();
  const { setItem } = useNeedActions();
  const filters = Object.entries(item.filters)
    .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : String(value)}`)
    .join(" · ");
  return (
    <li className="need-item">
      <div className="need-item__head">
        <div>
          <strong>{item.description}</strong>
          {filters ? <p className="muted">{filters}</p> : null}
        </div>
        <div className="need-item__state">
          <span className="muted">
            {t("needs.progress", { done: item.confirmed, wanted: item.count_wanted })}
          </span>
          {canEdit ? (
            <Select
              aria-label={t("needs.itemStatus")}
              value={item.status}
              onValueChange={(status) =>
                setItem.mutate({ id: item.id, status: status as keyof typeof ITEM_TONE })
              }
              options={(["open", "fulfilled", "dropped"] as const).map((value) => ({
                value,
                label: t(`needs.itemState.${value}`),
              }))}
            />
          ) : (
            <StatusChip tone={ITEM_TONE[item.status as keyof typeof ITEM_TONE]} plain>
              {t(`needs.itemState.${item.status}`)}
            </StatusChip>
          )}
        </div>
      </div>
      {item.candidates.length ? (
        <ul className="need-candidates" aria-label={t("needs.candidates")}>
          {item.candidates.map((candidate) => (
            <Candidate
              key={candidate.id}
              candidate={candidate}
              itemId={item.id}
              canEdit={canEdit}
            />
          ))}
        </ul>
      ) : (
        <p className="muted">{t("needs.noCandidates")}</p>
      )}
    </li>
  );
}

function Detail({ id, canEdit }: { id: string; canEdit: boolean }) {
  const { t } = useTranslation();
  const need = useNeed(id);
  const { setStatus } = useNeedActions();
  if (need.isLoading) return <Skeleton height={240} radius={16} />;
  if (!need.data) return null;
  const data = need.data;
  return (
    <Card
      title={data.title}
      actions={
        canEdit ? (
          <Button
            variant="secondary"
            loading={setStatus.isPending}
            onClick={() =>
              setStatus.mutate({ id, status: data.status === "open" ? "closed" : "open" })
            }
          >
            {data.status === "open" ? t("needs.close") : t("needs.reopen")}
          </Button>
        ) : undefined
      }
    >
      {data.purpose ? <p>{data.purpose}</p> : null}
      <p className="muted">
        {t("needs.createdBy", { who: t(`needs.creator.${data.created_by}`) })} ·{" "}
        {formatDateTime(data.created_at)}
      </p>
      {data.items.length ? (
        <ul className="need-items" aria-label={t("needs.items")}>
          {data.items.map((item) => (
            <Item key={item.id} item={item} canEdit={canEdit} />
          ))}
        </ul>
      ) : (
        <p className="muted">{t("needs.noItems")}</p>
      )}
    </Card>
  );
}

export function NeedsPage() {
  const { t } = useTranslation();
  const { list: id, state: status = "open" } = useSearch({ from: "/_app/needs" });
  const navigate = useNavigate();
  const me = useMe();
  const canEdit = me.data ? hasRole(me.data.role, "editor") : false;
  const lists = useNeedLists(status);
  const [creating, setCreating] = useState(false);
  const go = (search: { list?: string; state?: "open" | "closed" }) =>
    void navigate({ to: "/needs", search, replace: true });
  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">{t("needs.heading")}</h1>
          <p className="page__lead">{t("needs.lead")}</p>
        </div>
        {canEdit ? (
          <div className="page__actions">
            <Button icon={<Plus size={16} />} onClick={() => setCreating(true)}>
              {t("needs.create")}
            </Button>
          </div>
        ) : null}
      </header>
      <div className="needs-layout">
        <aside className="needs-lists">
          <div role="group" aria-label={t("needs.status")} className="needs-switch">
            {(["open", "closed"] as const).map((value) => (
              <Button
                key={value}
                size="sm"
                variant={status === value ? "secondary" : "ghost"}
                aria-pressed={status === value}
                onClick={() => go({ state: value })}
              >
                {t(`needs.${value}`)}
              </Button>
            ))}
          </div>
          {lists.isLoading ? (
            <Skeleton height={120} radius={16} />
          ) : lists.data?.items.length ? (
            <ul className="needs-list" aria-label={t("needs.lists")}>
              {lists.data.items.map((need) => (
                <li key={need.id}>
                  <button
                    type="button"
                    className="needs-list__row"
                    aria-current={need.id === id ? "true" : undefined}
                    onClick={() => go({ list: need.id, state: status })}
                  >
                    <strong>{need.title}</strong>
                    <span className="muted">
                      {t("needs.counts", {
                        items: Object.values(need.items).reduce((a, b) => a + b, 0),
                        candidates: need.candidates,
                      })}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              icon={<ClipboardList size={28} />}
              title={t("needs.emptyTitle")}
              description={t("needs.emptyText")}
            />
          )}
        </aside>
        <div className="needs-detail">
          {id ? <Detail id={id} canEdit={canEdit} /> : <p className="muted">{t("needs.pick")}</p>}
        </div>
      </div>
      <CreateDialog
        open={creating}
        onClose={(created) => {
          setCreating(false);
          if (created) go({ list: created, state: "open" });
        }}
      />
    </div>
  );
}
