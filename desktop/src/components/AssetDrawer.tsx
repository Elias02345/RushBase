import { ClipboardCopy, FolderSearch, GripHorizontal, Plus } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge, Button, Drawer, Skeleton } from "@rushbase/ui";

import { derivedPath } from "../bridge";
import { useBridge } from "../bridge/context";
import { formatClock } from "../lib/format";
import { useAsset } from "../state/library";
import { useSettings } from "../state/queries";

const BROWSER_CODECS = /^(h264|avc1?|vp8|vp9|av1|hevc|h265)/i;
const BROWSER_CONTAINERS = /\.(mp4|m4v|mov|webm)$/i;

interface Props {
  assetId: string | null;
  onClose: () => void;
  inBasket: boolean;
  onAdd: (id: string, shots?: number[]) => void;
}

/** Detail view: playback of the local original (proxy as fallback), shots, tags and path actions. */
export function AssetDrawer({ assetId, onClose, inBasket, onAdd }: Props) {
  const { t } = useTranslation();
  return (
    <Drawer
      open={assetId !== null}
      onOpenChange={(open) => !open && onClose()}
      title={t("library.detail")}
      closeLabel={t("common.close")}
      size="lg"
    >
      {assetId ? (
        <DrawerBody key={assetId} assetId={assetId} inBasket={inBasket} onAdd={onAdd} />
      ) : null}
    </Drawer>
  );
}

function DrawerBody({ assetId, inBasket, onAdd }: Omit<Props, "onClose"> & { assetId: string }) {
  const { t, i18n } = useTranslation();
  const bridge = useBridge();
  const { data: settings } = useSettings();
  const { data: asset, isPending, error } = useAsset(assetId);
  const video = useRef<HTMLVideoElement>(null);
  const [source, setSource] = useState<"original" | "proxy">("original");
  const [picked, setPicked] = useState<number[]>([]);
  const dragPath = asset?.local_exists ? asset.local_path : null;
  const { data: drag } = useQuery({
    queryKey: ["drag", dragPath],
    enabled: !!dragPath,
    queryFn: () => bridge.dragCheck(dragPath ?? ""),
  });

  const root = settings?.library_root ?? "";
  const canPlayOriginal =
    !!asset?.local_path &&
    asset.local_exists &&
    asset.media_type === "video" &&
    BROWSER_CONTAINERS.test(asset.local_path) &&
    BROWSER_CODECS.test(asset.codec ?? "");
  const proxyPath = asset ? derivedPath(root, asset.proxy_url) : null;
  const playing = source === "original" && canPlayOriginal ? asset?.local_path : proxyPath;
  const lang = i18n.language === "en" ? "en" : "de";

  return (
    <>
      {isPending ? <Skeleton height={240} /> : null}
      {error ? (
        <p role="alert" className="dk-error">
          {error.message}
        </p>
      ) : null}
      {asset ? (
        <div className="dk-detail">
          {asset.media_type === "video" && playing ? (
            <video
              ref={video}
              key={playing}
              className="dk-player"
              controls
              preload="metadata"
              src={bridge.assetUrl(playing)}
              onError={() => source === "original" && setSource("proxy")}
            />
          ) : asset.media_type === "photo" && asset.local_path ? (
            <img
              className="dk-player"
              src={bridge.assetUrl(asset.local_path)}
              alt={asset.filename}
            />
          ) : (
            <p className="dk-muted">{t("library.noPlayback")}</p>
          )}
          <div className="dk-badges">
            <Badge tone={playing === asset.local_path ? "success" : "info"}>
              {playing === asset.local_path ? t("library.original") : t("library.proxy")}
            </Badge>
            {asset.local_exists ? null : <Badge tone="warning">{t("library.notMounted")}</Badge>}
            {asset.width && asset.height ? (
              <Badge>
                {asset.width}×{asset.height}
              </Badge>
            ) : null}
            {asset.fps ? <Badge>{asset.fps} fps</Badge> : null}
            {asset.duration_ms ? <Badge>{formatClock(asset.duration_ms)}</Badge> : null}
          </div>
          <p>
            {(lang === "en" ? asset.ai.summary_short_en : asset.ai.summary_short_de) ??
              asset.summary}
          </p>
          {asset.ai.tags.length > 0 ? (
            <div className="dk-badges">
              {asset.ai.tags.map((tag) => (
                <Badge key={`${tag.field_key}:${tag.value}`}>
                  {(lang === "en" ? tag.label_en : tag.label_de) ?? tag.value}
                </Badge>
              ))}
            </div>
          ) : null}

          {asset.shots.length > 0 ? (
            <section aria-label={t("library.shots")}>
              <h3>{t("library.shots")}</h3>
              <ul className="dk-shots">
                {asset.shots.map((shot) => {
                  const frame = derivedPath(root, shot.keyframes[0]?.url ?? null);
                  return (
                    <li key={shot.id}>
                      <label>
                        <input
                          type="checkbox"
                          checked={picked.includes(shot.idx)}
                          onChange={(e) =>
                            setPicked(
                              e.target.checked
                                ? [...picked, shot.idx]
                                : picked.filter((i) => i !== shot.idx),
                            )
                          }
                          aria-label={t("library.pickShot", { index: shot.idx + 1 })}
                        />
                      </label>
                      <button
                        type="button"
                        className="dk-shot"
                        onClick={() => {
                          if (video.current) video.current.currentTime = shot.start_ms / 1000;
                        }}
                      >
                        {frame ? (
                          <img src={bridge.assetUrl(frame)} alt="" loading="lazy" />
                        ) : (
                          <span className="dk-shot__blank" />
                        )}
                        <span>
                          <strong>
                            {formatClock(shot.start_ms)}–{formatClock(shot.end_ms)}
                          </strong>
                          <span className="dk-muted">
                            {(lang === "en"
                              ? shot.ai?.description_short_en
                              : shot.ai?.description_short_de) ?? ""}
                          </span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          ) : null}

          <div className="dk-actions">
            <Button
              icon={<Plus size={16} />}
              onClick={() => onAdd(asset.id, picked.length > 0 ? picked : undefined)}
            >
              {inBasket
                ? t("library.updateBasket")
                : picked.length > 0
                  ? t("library.addShots", { count: picked.length })
                  : t("library.addToBasket")}
            </Button>
            {asset.local_path ? (
              <>
                <Button
                  variant="secondary"
                  icon={<ClipboardCopy size={16} />}
                  onClick={() => void bridge.copyText(asset.local_path ?? "")}
                >
                  {t("library.copyPath")}
                </Button>
                <Button
                  variant="secondary"
                  icon={<FolderSearch size={16} />}
                  disabled={!asset.local_exists}
                  onClick={() => void bridge.revealInFolder(asset.local_path ?? "")}
                >
                  {t("library.reveal")}
                </Button>
              </>
            ) : null}
            {drag?.ok && asset.local_path ? (
              <Button
                variant="ghost"
                icon={<GripHorizontal size={16} />}
                onClick={() => void bridge.startDrag([asset.local_path ?? ""])}
              >
                {t("library.dragOut")}
              </Button>
            ) : null}
          </div>
          {drag && !drag.ok ? (
            <p className="dk-muted">
              {t(`library.dragOff.${drag.reason ?? "unknown"}`, {
                defaultValue: t("library.dragOff.unknown"),
              })}
            </p>
          ) : null}
          <p className="dk-mono dk-muted dk-path">{asset.local_path}</p>
        </div>
      ) : null}
    </>
  );
}
