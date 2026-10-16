import { Button, EmptyState, Select, Skeleton, StatusChip, useToast } from "@rushbase/ui";
import { Link } from "@tanstack/react-router";
import { Check, ExternalLink, Lock, SearchCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useEditAsset, useReview } from "../api/aiQueries";
import type { AssetDetail, ReviewItem } from "../api/contracts";
import { useAsset } from "../api/queries";
import { groupTags, percent, tagLabel } from "../lib/ai";
import { formatDuration } from "../lib/format";

const ALL = "all";

/** Human-readable reason: `shot2:low_confidence:shot_size` becomes a translated phrase. */
function reasonLabel(
  t: (key: string, options?: Record<string, unknown>) => string,
  reason: string,
) {
  const match = /^(?:shot(\d+):)?([a-z_]+)(?::(.+))?$/.exec(reason);
  if (!match) return reason;
  const [, shot, kind = "", detail] = match;
  const base = t(`review.reason.${kind}`, {
    defaultValue: kind.replaceAll("_", " "),
    field: detail ? t(`ai.field.${detail}`, { defaultValue: detail }) : "",
    flag: detail ?? "",
  });
  return shot ? t("review.reasonShot", { n: Number(shot) + 1, reason: base }) : base;
}

function Detail({ item, detail }: { item: ReviewItem; detail: AssetDetail | undefined }) {
  const { t, i18n } = useTranslation();
  if (!detail) return <Skeleton height={320} radius={16} />;
  const groups = groupTags(detail.ai.tags);
  const summary = i18n.language === "en" ? detail.ai.summary_short_en : detail.ai.summary_short_de;
  return (
    <div className="review-detail">
      <div className="review-detail__frames">
        {detail.shots.length > 0 ? (
          detail.shots.slice(0, 6).map((shot) => {
            const frame = shot.keyframes.find((k) => k.position === "middle") ?? shot.keyframes[0];
            return frame?.url ? (
              <figure key={shot.idx}>
                <img src={frame.url} alt="" loading="lazy" />
                <figcaption>{formatDuration(shot.start_ms)}</figcaption>
              </figure>
            ) : null;
          })
        ) : detail.poster_url ? (
          <figure>
            <img src={detail.poster_url} alt="" />
          </figure>
        ) : null}
      </div>
      {summary ? <p>{summary}</p> : null}
      <ul className="review-detail__reasons" aria-label={t("review.reasons")}>
        {item.reasons.map((reason) => (
          <li key={reason}>
            <StatusChip tone="review" plain>
              {reasonLabel(t, reason)}
            </StatusChip>
          </li>
        ))}
      </ul>
      {groups.map((group) => (
        <p key={group.key} className="review-detail__tags">
          <strong>{i18n.language === "en" ? group.labelEn : group.labelDe}:</strong>{" "}
          {group.tags
            .map(
              (tag) =>
                `${tagLabel(tag, i18n.language)} (${tag.source}${tag.confidence !== null && tag.source !== "human" ? ` ${percent(tag.confidence)}` : ""})`,
            )
            .join(", ")}
        </p>
      ))}
    </div>
  );
}

/** Review queue: assets the AI was unsure about, with keyboard approval. */
export function ReviewPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const [reason, setReason] = useState(ALL);
  const queue = useReview(reason === ALL ? {} : { review_reason: reason });
  const items = useMemo(() => queue.data?.pages.flatMap((p) => p.items) ?? [], [queue.data]);
  const total = queue.data?.pages[0]?.total ?? null;
  const [index, setIndex] = useState(0);
  const current = items[Math.min(index, Math.max(0, items.length - 1))];
  const detail = useAsset(current?.asset.id ?? "").data;
  const edit = useEditAsset();
  const allReasons = useMemo(() => [...new Set(items.flatMap((i) => i.reasons))].sort(), [items]);

  const approve = useCallback(() => {
    if (!current) return;
    edit.mutate(
      { id: current.asset.id, review_done: true },
      {
        onSuccess: () => toast.push({ title: t("review.approved"), tone: "success" }),
        onError: () => toast.push({ title: t("common.error"), tone: "error" }),
      },
    );
  }, [current, edit, t, toast]);
  const lock = useCallback(() => {
    if (current) edit.mutate({ id: current.asset.id, locked: true, review_done: true });
  }, [current, edit]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key === "j" || event.key === "ArrowDown") {
        setIndex((i) => Math.min(items.length - 1, i + 1));
      } else if (event.key === "k" || event.key === "ArrowUp") {
        setIndex((i) => Math.max(0, i - 1));
      } else if (event.key === "a") {
        approve();
      } else if (event.key === "l") {
        lock();
      } else {
        return;
      }
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items.length, approve, lock]);

  return (
    <div className="page review-page">
      <header className="page__head">
        <div>
          <h1 className="page__title">{t("review.title")}</h1>
          <p className="page__lead">
            {total !== null ? t("review.count", { count: total }) : t("review.lead")}
          </p>
        </div>
        <Select
          aria-label={t("review.filterReason")}
          value={reason}
          onValueChange={(value) => {
            setReason(value);
            setIndex(0);
          }}
          options={[
            { value: ALL, label: t("review.allReasons") },
            ...allReasons.map((r) => ({ value: r, label: reasonLabel(t, r) })),
          ]}
        />
      </header>
      {queue.isLoading ? (
        <Skeleton height={320} radius={16} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={<SearchCheck size={28} />}
          title={t("review.emptyTitle")}
          description={t("review.emptyText")}
        />
      ) : (
        <div className="review-layout">
          <ul className="review-list" aria-label={t("review.queue")}>
            {items.map((item, i) => (
              <li key={item.asset.id}>
                <button
                  type="button"
                  className="review-list__item"
                  aria-current={item === current || undefined}
                  onClick={() => setIndex(i)}
                >
                  {item.asset.poster_url ? (
                    <img src={item.asset.poster_url} alt="" loading="lazy" width={96} height={54} />
                  ) : (
                    <span className="review-list__ph" aria-hidden="true" />
                  )}
                  <span>
                    <strong>{item.asset.title ?? item.asset.filename}</strong>
                    <span className="muted">
                      {t("review.reasonCount", { count: item.reasons.length })}
                    </span>
                  </span>
                </button>
              </li>
            ))}
            {queue.hasNextPage ? (
              <li>
                <Button variant="ghost" size="sm" onClick={() => void queue.fetchNextPage()}>
                  {t("review.more")}
                </Button>
              </li>
            ) : null}
          </ul>
          {current ? (
            <section
              className="review-panel"
              aria-label={current.asset.title ?? current.asset.filename}
            >
              <div className="review-panel__head">
                <h2>{current.asset.title ?? current.asset.filename}</h2>
                <div className="review-panel__actions">
                  <Button icon={<Check size={16} />} onClick={approve} loading={edit.isPending}>
                    {t("review.approve")} <kbd>A</kbd>
                  </Button>
                  <Button variant="secondary" icon={<Lock size={16} />} onClick={lock}>
                    {t("review.lock")} <kbd>L</kbd>
                  </Button>
                  <Link
                    to="/assets/$id"
                    params={{ id: current.asset.id }}
                    search={{}}
                    className="rb-button rb-button--ghost"
                  >
                    <ExternalLink size={16} aria-hidden="true" />
                    {t("review.correct")}
                  </Link>
                </div>
              </div>
              <Detail item={current} detail={detail} />
              <p className="muted review-panel__keys">{t("review.keys")}</p>
            </section>
          ) : null}
        </div>
      )}
    </div>
  );
}
