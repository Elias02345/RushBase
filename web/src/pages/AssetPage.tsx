import {
  Button,
  EmptyState,
  IconButton,
  Skeleton,
  StatusChip,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  useToast,
} from "@rushbase/ui";
import { Link, useParams, useRouter } from "@tanstack/react-router";
import { ArrowLeft, Captions, Copy, FileSearch, Tags } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import type { AssetDetail, Shot } from "../api/contracts";
import { useAsset } from "../api/queries";
import {
  formatBytes,
  formatDate,
  formatDateTime,
  formatDuration,
  formatResolution,
} from "../lib/format";
import { detectOs, copyText, type Os } from "../lib/platform";
import { assetTone } from "../lib/status";

function humanize(key: string): string {
  return key.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}

function MetadataGroups({ detail }: { detail: AssetDetail }) {
  const { t } = useTranslation();
  const normalized = detail.metadata.normalized;
  const groups: [string, [string, string][]][] = [];
  const loose: [string, string][] = [];
  for (const [key, value] of Object.entries(normalized)) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const rows = Object.entries(value as Record<string, unknown>)
        .filter(([, v]) => v !== null && v !== undefined && v !== "")
        .map(([k, v]): [string, string] => [k, String(v)]);
      if (rows.length > 0) groups.push([key, rows]);
    } else if (value !== null && value !== undefined && value !== "") {
      loose.push([key, String(value)]);
    }
  }
  if (loose.length > 0) groups.push(["other", loose]);
  if (groups.length === 0) {
    return <EmptyState compact title={t("asset.noMetadata")} description={t("asset.noMetadataText")} />;
  }
  return (
    <div className="meta-groups">
      {groups.map(([group, rows]) => (
        <section key={group} className="meta-group">
          <h3>{t(`meta.groups.${group}`, { defaultValue: humanize(group) })}</h3>
          <dl>
            {rows.map(([key, value]) => (
              <div key={key}>
                <dt>{t(`meta.fields.${key}`, { defaultValue: humanize(key) })}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  );
}

function FilesPanel({ detail }: { detail: AssetDetail }) {
  const { t } = useTranslation();
  const toast = useToast();
  const [os, setOs] = useState<Os>(detectOs());
  const copy = async (text: string) => {
    const ok = await copyText(text);
    toast.push({ title: ok ? t("common.copied") : t("common.copyFailed"), tone: ok ? "success" : "error" });
  };
  return (
    <div>
      <div className="segmented segmented--text" role="group" aria-label={t("asset.pathFor")}>
        {(["windows", "macos", "linux"] as const).map((key) => (
          <button
            key={key}
            type="button"
            aria-pressed={os === key}
            className="segmented__btn"
            onClick={() => setOs(key)}
          >
            {t(`os.${key}`)}
          </button>
        ))}
      </div>
      <ul className="file-list">
        {detail.files.map((file) => {
          const path = file.paths[os] ?? file.rel_path;
          return (
            <li key={file.rel_path}>
              <div className="file-list__head">
                <StatusChip tone="neutral" plain>
                  {t(`fileRole.${file.role}`, { defaultValue: file.role })}
                </StatusChip>
                <span className="muted">{formatBytes(file.size)}</span>
              </div>
              <code className="codeblock codeblock--path">{path}</code>
              <div className="file-list__actions">
                <Button size="sm" variant="secondary" icon={<Copy size={14} />} onClick={() => void copy(path)}>
                  {t("asset.copyPath")}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => void copy(file.rel_path)}>
                  {t("asset.copyRelative")}
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ShotTimeline({
  detail,
  current,
  onSeek,
}: {
  detail: AssetDetail;
  current: number;
  onSeek: (ms: number) => void;
}) {
  const { t } = useTranslation();
  const total = detail.duration_ms ?? 0;
  if (detail.shots.length === 0 || total === 0) return null;
  const label = (shot: Shot) =>
    t("asset.shotLabel", { n: shot.idx + 1, start: formatDuration(shot.start_ms), end: formatDuration(shot.end_ms) });
  return (
    <div className="timeline">
      <h3 className="timeline__title">{t("asset.shots", { count: detail.shots.length })}</h3>
      <div className="timeline__track">
        {detail.shots.map((shot) => {
          const active = current >= shot.start_ms && current < shot.end_ms;
          return (
            <button
              key={shot.idx}
              type="button"
              className={`timeline__shot${active ? " timeline__shot--active" : ""}`}
              style={{ flexGrow: shot.end_ms - shot.start_ms }}
              aria-label={label(shot)}
              aria-current={active || undefined}
              title={label(shot)}
              onClick={() => onSeek(shot.start_ms)}
            />
          );
        })}
        <span className="timeline__playhead" style={{ left: `${Math.min(100, (current / total) * 100)}%` }} aria-hidden="true" />
      </div>
      <ul className="keyframes" aria-label={t("asset.keyframes")}>
        {detail.shots.flatMap((shot) =>
          shot.keyframes.map((frame) => (
            <li key={frame.url}>
              <button type="button" onClick={() => onSeek(frame.t_ms)} aria-label={t("asset.jumpTo", { time: formatDuration(frame.t_ms) })}>
                <img src={frame.url} alt="" loading="lazy" width={160} height={90} />
                <span>{formatDuration(frame.t_ms)}</span>
              </button>
            </li>
          )),
        )}
      </ul>
    </div>
  );
}

function Stage({ detail }: { detail: AssetDetail }) {
  const { t } = useTranslation();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [current, setCurrent] = useState(0);
  const ratio = detail.width && detail.height ? detail.width / detail.height : 16 / 9;
  const seek = (ms: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = ms / 1000;
    setCurrent(ms);
  };
  return (
    <div className="stage">
      <div className="stage__frame" style={{ aspectRatio: Math.min(ratio, 2.4) }}>
        {detail.proxy_url && detail.media_type === "video" ? (
          <video
            ref={videoRef}
            src={detail.proxy_url}
            poster={detail.poster_url ?? undefined}
            controls
            preload="metadata"
            onTimeUpdate={(event) => setCurrent(event.currentTarget.currentTime * 1000)}
            aria-label={detail.title ?? detail.filename}
          />
        ) : detail.poster_url ? (
          <>
            <img src={detail.poster_url} alt={detail.title ?? detail.filename} />
            {detail.media_type === "video" ? <p className="stage__note">{t("asset.noProxy")}</p> : null}
          </>
        ) : (
          <p className="stage__note stage__note--center">{t("asset.noPreview")}</p>
        )}
      </div>
      <ShotTimeline detail={detail} current={current} onSeek={seek} />
    </div>
  );
}

export function AssetPage() {
  const { t } = useTranslation();
  const { id } = useParams({ from: "/assets/$id" });
  const router = useRouter();
  const query = useAsset(id);
  const detail = query.data;

  if (query.isError) {
    const missing = query.error instanceof ApiError && query.error.status === 404;
    return (
      <div className="page">
        <EmptyState
          icon={<FileSearch size={28} />}
          title={missing ? t("asset.missingTitle") : t("library.errorTitle")}
          description={missing ? t("asset.missingText") : t("library.errorText")}
          action={
            <Link to="/library" className="rb-button rb-button--primary">
              {t("asset.backToLibrary")}
            </Link>
          }
        />
      </div>
    );
  }

  return (
    <div className="page asset-page">
      <div className="asset-page__back">
        <IconButton
          label={t("asset.back")}
          icon={<ArrowLeft size={18} />}
          variant="secondary"
          onClick={() => router.history.back()}
        />
        <Link to="/library" className="link">
          {t("asset.backToLibrary")}
        </Link>
      </div>
      {!detail ? (
        <div className="asset-layout">
          <Skeleton style={{ aspectRatio: "16 / 9", width: "100%", height: "auto" }} radius={22} />
          <Skeleton height={360} radius={22} />
        </div>
      ) : (
        <>
          <header className="page__head">
            <div>
              <h1 className="page__title">{detail.title ?? detail.filename}</h1>
              <p className="page__lead">
                {[
                  detail.title ? detail.filename : null,
                  formatResolution(detail.width, detail.height),
                  detail.duration_ms ? formatDuration(detail.duration_ms) : null,
                  detail.capture_at ? formatDate(detail.capture_at) : null,
                  formatBytes(detail.size_total),
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            </div>
            <StatusChip tone={assetTone(detail.status)}>{t(`assetStatus.${detail.status}`)}</StatusChip>
          </header>
          <div className="asset-layout">
            <Stage detail={detail} key={detail.id} />
            <Tabs defaultValue="data" className="asset-tabs">
              <TabsList aria-label={t("asset.sections")}>
                <TabsTrigger value="data">{t("asset.tabs.data")}</TabsTrigger>
                <TabsTrigger value="files">{t("asset.tabs.files")}</TabsTrigger>
                <TabsTrigger value="history">{t("asset.tabs.history")}</TabsTrigger>
                <TabsTrigger value="transcript">{t("asset.tabs.transcript")}</TabsTrigger>
                <TabsTrigger value="tags">{t("asset.tabs.tags")}</TabsTrigger>
              </TabsList>
              <TabsContent value="data">
                <MetadataGroups detail={detail} />
              </TabsContent>
              <TabsContent value="files">
                <FilesPanel detail={detail} />
              </TabsContent>
              <TabsContent value="history">
                <ol className="history">
                  {detail.history.map((entry) => (
                    <li key={`${entry.at}-${entry.action}`}>
                      <span className="history__action">
                        {t(`history.${entry.action}`, { defaultValue: entry.action })}
                      </span>
                      <span className="muted">{formatDateTime(entry.at)}</span>
                    </li>
                  ))}
                </ol>
              </TabsContent>
              <TabsContent value="transcript">
                <EmptyState
                  compact
                  icon={<Captions size={24} />}
                  title={t("asset.transcriptTitle")}
                  description={t("asset.transcriptText")}
                />
              </TabsContent>
              <TabsContent value="tags">
                <EmptyState
                  compact
                  icon={<Tags size={24} />}
                  title={t("asset.tagsTitle")}
                  description={t("asset.tagsText")}
                />
              </TabsContent>
            </Tabs>
          </div>
        </>
      )}
    </div>
  );
}
