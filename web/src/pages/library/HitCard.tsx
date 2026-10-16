import { StatusChip } from "@rushbase/ui";
import { Link } from "@tanstack/react-router";
import { Film, Image, Music } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { SearchHit } from "../../api/contracts";
import { timecode } from "../../lib/ai";
import { assetTone } from "../../lib/status";

const ICONS = { video: Film, photo: Image, audio: Music, other: Film } as const;

/** A search hit: the keyframe of the scene (or the poster of the asset) with timecode and text. */
export function HitCard({ hit }: { hit: SearchHit }) {
  const { t, i18n } = useTranslation();
  const { asset, shot } = hit;
  const image = shot?.keyframe_url ?? asset.poster_url;
  const Icon = ICONS[asset.media_type as keyof typeof ICONS] ?? Film;
  const description = shot
    ? ((i18n.language === "en" ? shot.description_en : shot.description_de) ??
      shot.description_de ??
      shot.description_en)
    : asset.summary;
  return (
    <li className="hit-card">
      <Link
        to="/assets/$id"
        params={{ id: asset.id }}
        search={shot ? { t: shot.start_ms } : {}}
        className="hit-card__link"
        aria-label={[asset.title ?? asset.filename, shot ? timecode(shot.start_ms) : null]
          .filter(Boolean)
          .join(", ")}
      >
        <span className="hit-card__image">
          {image ? (
            <img src={image} alt="" loading="lazy" width={320} height={180} />
          ) : (
            <Icon size={28} aria-hidden="true" />
          )}
          {shot ? (
            <span className="hit-card__time">
              {timecode(shot.start_ms)} – {timecode(shot.end_ms)}
            </span>
          ) : null}
        </span>
        <span className="hit-card__body">
          <strong>{asset.title ?? asset.filename}</strong>
          {description ? <span className="hit-card__text">{description}</span> : null}
          <span className="hit-card__meta">
            <StatusChip tone={assetTone(asset.status)} plain>
              {t(`assetStatus.${asset.status}`)}
            </StatusChip>
            {Object.keys(hit.ranks).length > 1 ? (
              <span className="muted">
                {t("search.agreement", { count: Object.keys(hit.ranks).length })}
              </span>
            ) : null}
          </span>
        </span>
      </Link>
    </li>
  );
}
