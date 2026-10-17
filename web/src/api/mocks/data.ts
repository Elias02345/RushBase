import type {
  AssetDetail,
  AssetStatus,
  AssetSummary,
  Facet,
  FilingRule,
  MediaType,
  Orientation,
  Shot,
} from "../contracts";
import type { Batch, SystemStatus, User, Worker } from "../types";

/** Library roots per operating system as stored by `PUT /admin/library-settings`. */
export type PathRoots = Record<string, string | null>;

/** Deterministic pseudo-random numbers so screenshots and tests are stable. */
/** Frames of the mock contact sheet (one row, like the server's). */
export const SPRITE_TILES = 10;

export function createRandom(seed: number) {
  let state = seed >>> 0;
  const next = () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return {
    next,
    int: (min: number, max: number) => Math.floor(next() * (max - min + 1)) + min,
    pick: <T>(items: readonly T[]): T => items[Math.floor(next() * items.length)] as T,
  };
}

const NOW = Date.UTC(2026, 9, 12, 14, 30);
const DAY = 86_400_000;

const CAMERAS = [
  "Sony ILCE-7SM3",
  "Sony ILCE-7M4",
  "Panasonic DC-GH6",
  "DJI Mavic 3 Pro",
  "Canon EOS R5",
  "Apple iPhone 15 Pro",
] as const;
const LENSES = [
  "FE 24-70mm F2.8 GM",
  "FE 35mm F1.4 GM",
  "Lumix S 85mm F1.8",
  "RF 15-35mm F2.8L",
  "Built-in wide",
] as const;
const LOG_PROFILES = ["S-Log3", "V-Log", "D-Log", "HLG", "Rec.709"] as const;
const CODECS = ["XAVC S-I", "H.264", "H.265", "ProRes 422", "JPEG", "HEIC"] as const;
const TITLES = [
  "Morning mist over the lake",
  "Harbour at golden hour",
  "Interview, wide",
  "Drone: ridge line",
  "Market stalls",
  "Rooftop B-roll",
  "Cable car ascent",
  "Studio pickup shots",
  "Forest path, slow pan",
  "Night street reflections",
  "Close-up hands, workshop",
  "Glacier edge",
] as const;
const BATCH_LABELS = [
  "Lake trip",
  "Alpine drone day",
  "Interview Weber",
  "Card A7S3 01",
  "City B-roll",
] as const;

export const MOCK_BATCH_IDS = BATCH_LABELS.map(
  (_, index) => `b0000000-0000-4000-8000-00000000000${index + 1}`,
);

function uuid(index: number): string {
  return `a${index.toString(16).padStart(7, "0")}-0000-4000-8000-${index.toString(16).padStart(12, "0")}`;
}

function filename(media: MediaType, index: number, camera: string): string {
  if (media === "photo")
    return `IMG_${(2000 + index).toString()}.${camera.includes("Apple") ? "HEIC" : "JPG"}`;
  if (media === "audio") return `REC_${(100 + index).toString()}.wav`;
  if (camera.includes("Sony")) return `C${(40 + index).toString().padStart(4, "0")}.MP4`;
  if (camera.includes("DJI")) return `DJI_${(20260000 + index).toString()}_D.MP4`;
  return `A001_C${(index % 900).toString().padStart(3, "0")}_0314.mov`;
}

function statusFor(random: ReturnType<typeof createRandom>, index: number): AssetStatus {
  if (index < 5) return "quarantined";
  if (index < 14) return "review";
  if (index < 26) return "processing";
  if (index < 30) return "ingesting";
  if (index < 34) return "duplicate";
  return random.next() < 0.97 ? "filed" : "review";
}

/** The mock library: 260 assets spread over a year. */
export function buildAssets(): AssetSummary[] {
  const random = createRandom(2026);
  const assets: AssetSummary[] = [];
  for (let index = 0; index < 260; index++) {
    const roll = random.next();
    const media: MediaType = roll < 0.68 ? "video" : roll < 0.9 ? "photo" : "audio";
    const camera = random.pick(CAMERAS);
    const status = statusFor(random, index);
    const orientation: Orientation | null =
      media === "audio" ? null : random.next() < 0.12 ? "portrait" : "landscape";
    const portrait = orientation === "portrait";
    const is4k = random.next() < 0.45;
    const width = media === "audio" ? null : portrait ? (is4k ? 2160 : 1080) : is4k ? 3840 : 1920;
    const height = media === "audio" ? null : portrait ? (is4k ? 3840 : 1920) : is4k ? 2160 : 1080;
    const durationMs =
      media === "video"
        ? random.int(4, 410) * 1000 + random.int(0, 999)
        : media === "audio"
          ? random.int(30, 900) * 1000
          : null;
    const hasMedia = status !== "ingesting";
    const analysed =
      hasMedia && (status === "filed" || status === "review" || status === "duplicate");
    const id = uuid(index + 1);
    assets.push({
      id,
      media_type: media,
      status,
      title: random.next() < 0.45 ? random.pick(TITLES) : null,
      filename: filename(media, index, camera),
      capture_at: new Date(
        NOW - random.int(0, 330) * DAY - random.int(0, 80_000) * 1000,
      ).toISOString(),
      duration_ms: durationMs,
      width,
      height,
      fps: media === "video" ? random.pick([23.976, 25, 25, 29.97, 50, 59.94]) : null,
      codec:
        media === "audio"
          ? "PCM"
          : media === "photo"
            ? random.pick(["JPEG", "HEIC"])
            : random.pick(CODECS.slice(0, 4)),
      camera_model: media === "audio" ? null : camera,
      lens_model: media === "audio" ? null : random.pick(LENSES),
      log_profile: media === "video" ? random.pick(LOG_PROFILES) : null,
      orientation,
      size_total:
        media === "video"
          ? Math.round(((durationMs ?? 0) / 1000) * (is4k ? 9_500_000 : 3_200_000))
          : media === "audio"
            ? Math.round(((durationMs ?? 0) / 1000) * 192_000)
            : random.int(2_000_000, 38_000_000),
      poster_url: media === "audio" || !analysed ? null : `/mock/media/poster/${id}.svg`,
      sprite_url: media === "video" && analysed ? `/mock/media/sprite/${id}.svg` : null,
      sprite_tiles: media === "video" && analysed ? SPRITE_TILES : null,
      batch_label: BATCH_LABELS[index % BATCH_LABELS.length] ?? null,
      review_state: status === "filed" && index % 9 === 0 ? "pending" : "none",
      locked: false,
      summary: null,
    });
  }
  return assets;
}

export function batchIdForLabel(label: string | null): string | null {
  const index = BATCH_LABELS.findIndex((candidate) => candidate === label);
  return index >= 0 ? (MOCK_BATCH_IDS[index] ?? null) : null;
}

const WINDOWS_ROOT = "\\\\nas\\media";
const MACOS_ROOT = "/Volumes/media";
const LINUX_ROOT = "/mnt/media";

/** The detail without its AI part (`withAi` in `ai.ts` completes it). */
export function buildDetail(summary: AssetSummary, mapping: PathRoots): Omit<AssetDetail, "ai"> {
  const date = (summary.capture_at ?? new Date(NOW).toISOString()).slice(0, 10);
  const [year = "2026", month = "01"] = date.split("-");
  const dir = `Content/${year}/${month}/${summary.camera_model?.split(" ")[0] ?? "Misc"}`;
  const toPaths = (rel: string) => {
    const out = {
      windows: mapping.windows ?? WINDOWS_ROOT,
      macos: mapping.macos ?? MACOS_ROOT,
      linux: mapping.linux ?? LINUX_ROOT,
    };
    return {
      windows: `${out.windows}\\${rel.replaceAll("/", "\\")}`,
      macos: `${out.macos}/${rel}`,
      linux: `${out.linux}/${rel}`,
    };
  };
  const filed = summary.status === "filed" || summary.status === "review";
  const rel = filed
    ? `${dir}/${summary.filename}`
    : `Input/${summary.batch_label ?? "Unsorted"}/${summary.filename}`;
  const random = createRandom(
    summary.id.length * 7919 + Number.parseInt(summary.id.slice(1, 8), 16),
  );
  const durationMs = summary.duration_ms ?? 0;
  const shotCount =
    summary.media_type === "video" ? Math.max(1, Math.min(9, Math.round(durationMs / 22_000))) : 0;
  const shots: Shot[] = [];
  let cursor = 0;
  for (let idx = 0; idx < shotCount; idx++) {
    const length = idx === shotCount - 1 ? durationMs - cursor : Math.round(durationMs / shotCount);
    const end = Math.min(durationMs, cursor + length);
    shots.push({
      id: `s${summary.id.slice(1)}-${idx}`,
      idx,
      start_ms: cursor,
      end_ms: end,
      keyframes: (
        [
          [0.15, "start"],
          [0.5, "middle"],
          [0.85, "end"],
        ] as const
      ).map(([at, position], k) => ({
        t_ms: Math.round(cursor + (end - cursor) * at),
        url: `/mock/media/keyframe/${summary.id}-${idx * 3 + k}.svg`,
        position,
      })),
      camera_motion: random.pick(["static", "pan", "tilt", "handheld", "push-in", null]),
      sharpness: Math.round(random.next() * 100) / 100,
      shake: Math.round(random.next() * 100) / 100,
    });
    cursor = end;
  }
  const isVideo = summary.media_type === "video";
  const normalized: Record<string, unknown> = {
    camera: {
      make: summary.camera_model?.split(" ")[0] ?? null,
      model: summary.camera_model,
      serial_number: summary.camera_model ? `0${random.int(1000000, 9999999)}` : null,
    },
    lens: {
      model: summary.lens_model,
      focal_length_mm: summary.lens_model ? random.pick([24, 35, 50, 85]) : null,
      aperture: summary.lens_model ? random.pick([1.4, 2.0, 2.8, 4.0]) : null,
    },
    exposure:
      summary.media_type === "audio"
        ? {}
        : {
            iso: random.pick([100, 400, 800, 1600, 3200]),
            shutter: isVideo
              ? `1/${(summary.fps ?? 25) * 2}`
              : `1/${random.pick([125, 250, 500, 1000])}`,
            white_balance_k: random.pick([3200, 4300, 5600, 6500]),
          },
    video: isVideo
      ? {
          codec: summary.codec,
          resolution: `${summary.width} x ${summary.height}`,
          fps: summary.fps,
          bit_depth: random.pick([8, 10]),
          bitrate_mbps: random.int(35, 250),
          color_space: summary.log_profile === "Rec.709" ? "Rec.709" : "S-Gamut3.Cine",
          log_profile: summary.log_profile,
        }
      : summary.media_type === "photo"
        ? { resolution: `${summary.width} x ${summary.height}`, codec: summary.codec }
        : {},
    audio:
      isVideo || summary.media_type === "audio"
        ? { codec: "PCM s24le", channels: 2, sample_rate_hz: 48_000 }
        : {},
    location:
      random.next() < 0.6
        ? {
            latitude: Number((47.2 + random.next()).toFixed(5)),
            longitude: Number((8.1 + random.next()).toFixed(5)),
            altitude_m: random.int(300, 2400),
          }
        : {},
  };
  const base = new Date(summary.capture_at ?? NOW).getTime();
  return {
    ...summary,
    sync: [],
    usage: [],
    files: [
      {
        role: "original",
        rel_path: rel,
        size: summary.size_total,
        blake3: random.int(0, 1e9).toString(16).padStart(8, "0").repeat(8),
        paths: toPaths(rel),
      },
      ...(isVideo
        ? [
            {
              role: "sidecar",
              rel_path: rel.replace(/\.[^.]+$/, ".XML"),
              size: 3_412,
              blake3: null,
              paths: toPaths(rel.replace(/\.[^.]+$/, ".XML")),
            },
          ]
        : []),
    ],
    metadata: { normalized },
    shots,
    proxy_url: isVideo && summary.poster_url ? "/mock/proxy.webm" : null,
    waveform_url: null,
    content_rel_dir: filed ? dir : null,
    tech_summary_de: null,
    tech_summary_en: null,
    decode_status: "ok",
    duplicate_of: summary.status === "duplicate" ? uuid(40) : null,
    history: [
      {
        at: new Date(base + 3_600_000).toISOString(),
        action: "ingest.grouped",
        details: { batch: summary.batch_label },
      },
      {
        at: new Date(base + 3_650_000).toISOString(),
        action: "stage.probe",
        details: { ok: true, duration_ms: 420 },
      },
      {
        at: new Date(base + 3_700_000).toISOString(),
        action: "stage.preview",
        details: { ok: true, duration_ms: 18_400 },
      },
      ...(filed
        ? [
            {
              at: new Date(base + 3_800_000).toISOString(),
              action: "filing.moved",
              details: { to: rel },
            },
          ]
        : []),
    ],
  };
}

export function buildFacets(assets: AssetSummary[]): Facet[] {
  const count = (pick: (a: AssetSummary) => string | null) => {
    const map = new Map<string, number>();
    for (const asset of assets) {
      const value = pick(asset);
      if (value) map.set(value, (map.get(value) ?? 0) + 1);
    }
    return [...map.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([value, n]) => ({ value, count: n }));
  };
  const times = assets
    .map((a) => a.capture_at)
    .filter((v): v is string => Boolean(v))
    .sort();
  return [
    {
      key: "media_type",
      label_de: "Medientyp",
      label_en: "Media type",
      kind: "enum",
      values: count((a) => a.media_type),
    },
    {
      key: "status",
      label_de: "Status",
      label_en: "Status",
      kind: "enum",
      values: count((a) => a.status),
    },
    {
      key: "camera_model",
      label_de: "Kamera",
      label_en: "Camera",
      kind: "enum",
      values: count((a) => a.camera_model),
    },
    {
      key: "lens_model",
      label_de: "Objektiv",
      label_en: "Lens",
      kind: "enum",
      values: count((a) => a.lens_model),
    },
    {
      key: "log_profile",
      label_de: "Log-Profil",
      label_en: "Log profile",
      kind: "enum",
      values: count((a) => a.log_profile),
    },
    {
      key: "orientation",
      label_de: "Ausrichtung",
      label_en: "Orientation",
      kind: "enum",
      values: count((a) => a.orientation),
    },
    {
      key: "capture_at",
      label_de: "Aufnahmedatum",
      label_en: "Capture date",
      kind: "date",
      min: times[0],
      max: times[times.length - 1],
    },
  ];
}

export function buildUsers(): User[] {
  return [
    {
      id: "u0000000-0000-4000-8000-000000000001",
      username: "elias",
      display_name: "Elias",
      role: "admin",
      locale: "de",
      disabled: false,
      last_login_at: new Date(NOW - 3_600_000).toISOString(),
    },
    {
      id: "u0000000-0000-4000-8000-000000000002",
      username: "mara",
      display_name: "Mara Voss",
      role: "editor",
      locale: "de",
      disabled: false,
      last_login_at: new Date(NOW - 2 * DAY).toISOString(),
    },
    {
      id: "u0000000-0000-4000-8000-000000000003",
      username: "jonas",
      display_name: "Jonas Weber",
      role: "viewer",
      locale: "en",
      disabled: false,
      last_login_at: null,
    },
  ];
}

export function buildWorkers(online: boolean): Worker[] {
  const seen = (ms: number) => new Date(NOW - ms).toISOString();
  return [
    {
      id: "w0000000-0000-4000-8000-000000000001",
      name: "studio-pc",
      hostname: "STUDIO-PC",
      platform: "Windows 11",
      version: "0.1.0",
      status: online ? "online" : "offline",
      online,
      last_seen_at: seen(online ? 4_000 : 6 * 3_600_000),
      last_stage: "preview",
      current_job_id: online ? "j0000000-0000-4000-8000-000000000001" : null,
      capabilities: {
        backends: ["cuda", "cpu"],
        vram_mb: 16384,
        ram_mb: 65536,
        stages: ["probe", "hash", "preview", "shots"],
      },
    },
    {
      id: "w0000000-0000-4000-8000-000000000002",
      name: "edit-laptop",
      hostname: "mbp-edit",
      platform: "macOS 15",
      version: "0.1.0",
      status: "offline",
      online: false,
      last_seen_at: seen(3 * DAY),
      last_stage: "hash",
      current_job_id: null,
      capabilities: { backends: ["cpu"], ram_mb: 32768, stages: ["probe", "hash", "preview"] },
    },
  ];
}

export function buildBatches(): Batch[] {
  const at = (days: number) => new Date(NOW - days * DAY).toISOString();
  return [
    {
      id: MOCK_BATCH_IDS[0] as string,
      input_path: "Input/Lake trip",
      label: "Lake trip",
      hint_date: "2026-10-03",
      status: "processing",
      priority: 5,
      paused: false,
      first_seen_at: at(2),
      completed_at: null,
    },
    {
      id: MOCK_BATCH_IDS[1] as string,
      input_path: "Input/Alpine drone day",
      label: "Alpine drone day",
      hint_date: "2026-09-28",
      status: "waiting",
      priority: 0,
      paused: true,
      first_seen_at: at(4),
      completed_at: null,
    },
    {
      id: MOCK_BATCH_IDS[2] as string,
      input_path: "Input/Interview Weber",
      label: "Interview Weber",
      hint_date: null,
      status: "done",
      priority: 0,
      paused: false,
      first_seen_at: at(9),
      completed_at: at(8),
    },
    {
      id: MOCK_BATCH_IDS[3] as string,
      input_path: "Input/Card A7S3 01",
      label: "Card A7S3 01",
      hint_date: "2026-10-11",
      status: "scanning",
      priority: 0,
      paused: false,
      first_seen_at: at(0.2),
      completed_at: null,
    },
    {
      id: MOCK_BATCH_IDS[4] as string,
      input_path: "Input/@auto/2026-10-09",
      label: "City B-roll",
      hint_date: "2026-10-09",
      status: "done",
      priority: -5,
      paused: false,
      first_seen_at: at(3),
      completed_at: at(2.5),
    },
  ];
}

export function buildStatus(workersOnline: number, paused: boolean): SystemStatus {
  const stage = (
    queued: number,
    leased: number,
    done: number,
    bytes: number,
    duration: number,
  ) => ({
    jobs: { queued, leased, done },
    pending_jobs: queued + leased,
    pending_files: queued + leased,
    pending_bytes: bytes,
    pending_duration_ms: duration,
    duration_unknown_jobs: 0,
  });
  return {
    version: "0.1.0",
    queue_paused: paused,
    stages: {
      probe: stage(0, 0, 258, 0, 0),
      hash: stage(6, 1, 240, 2_400_000_000, 0),
      preview: stage(14, 2, 224, 9_800_000_000, 3_140_000),
      shots: stage(18, 0, 205, 11_200_000_000, 3_980_000),
      finalize: stage(3, 0, 190, 600_000_000, 0),
    },
    assets: {
      filed: 214,
      processing: 12,
      review: 9,
      quarantined: 5,
      ingesting: 4,
      duplicate: 4,
      trashed: 0,
    },
    input_files: { copying: 3, stable: 14, grouped: 258, ignored: 12, gone: 1 },
    batches: { scanning: 1, waiting: 1, processing: 1, done: 2, error: 0 },
    workers_online: workersOnline,
    workers_total: 2,
  };
}

export function defaultPathRoots(): PathRoots {
  return { windows: WINDOWS_ROOT, macos: MACOS_ROOT, linux: LINUX_ROOT };
}

/** Rule versions, newest first; version 2 is active. */
export function defaultFilingRules(): FilingRule[] {
  return [
    {
      id: "f0000000-0000-4000-8000-000000000002",
      version: 2,
      template:
        "Content/{media_type}/{capture_date:%Y}/{capture_date:%Y-%m-%d}_{batch_label|Unsorted}/",
      fallbacks: {},
      active: true,
      note: "by year and shooting day",
      created_at: "2026-09-20T10:00:00Z",
    },
    {
      id: "f0000000-0000-4000-8000-000000000001",
      version: 1,
      template: "Content/{media_type}/{capture_date:%Y-%m-%d}/",
      fallbacks: {},
      active: false,
      note: "default layout",
      created_at: "2026-09-01T10:00:00Z",
    },
  ];
}
