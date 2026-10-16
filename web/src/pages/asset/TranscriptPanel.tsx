import { EmptyState, SearchField } from "@rushbase/ui";
import { Captions } from "lucide-react";
import { Fragment, useState } from "react";
import { useTranslation } from "react-i18next";

import { useTranscript } from "../../api/aiQueries";
import type { AssetDetail } from "../../api/contracts";
import { timecode } from "../../lib/ai";

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function Highlight({ text, query }: { text: string; query: string }) {
  const needle = query.trim();
  if (!needle) return <>{text}</>;
  const parts = text.split(new RegExp(`(${escapeRegExp(needle)})`, "i"));
  return (
    <>
      {parts.map((part, index) =>
        index % 2 === 1 ? <mark key={index}>{part}</mark> : <Fragment key={index}>{part}</Fragment>,
      )}
    </>
  );
}

/** Transcript with search inside the text; clicking a segment seeks the player. */
export function TranscriptPanel({
  detail,
  current,
  onSeek,
}: {
  detail: AssetDetail;
  current: number;
  onSeek: (ms: number) => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const available = detail.ai.transcript_segments > 0;
  const transcript = useTranscript(detail.id, query.trim(), available);
  if (!available) {
    return (
      <EmptyState
        compact
        icon={<Captions size={24} />}
        title={t("asset.transcriptTitle")}
        description={t("asset.transcriptText")}
      />
    );
  }
  const segments = transcript.data?.segments ?? [];
  return (
    <div className="transcript">
      <SearchField
        value={query}
        onValueChange={setQuery}
        clearLabel={t("common.clear")}
        aria-label={t("asset.transcriptSearch")}
        placeholder={t("asset.transcriptSearch")}
      />
      <p className="muted transcript__count" aria-live="polite">
        {t("asset.transcriptCount", {
          shown: segments.length,
          total: transcript.data?.total_segments ?? detail.ai.transcript_segments,
        })}
      </p>
      <ol className="transcript__list">
        {segments.map((segment) => {
          const active = current >= segment.start_ms && current < segment.end_ms;
          return (
            <li key={segment.idx}>
              <button
                type="button"
                className={`transcript__segment${active ? " transcript__segment--active" : ""}`}
                aria-current={active || undefined}
                onClick={() => onSeek(segment.start_ms)}
              >
                <span className="transcript__time">{timecode(segment.start_ms)}</span>
                <span>
                  <Highlight text={segment.text} query={query} />
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
