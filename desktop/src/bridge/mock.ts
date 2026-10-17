import type { Bridge } from "./bridge";
import type {
  AssetDetail,
  AssetSummary,
  Basket,
  CliResult,
  Envelope,
  Facet,
  Overview,
  Settings,
  StreamName,
} from "./types";

/** Scenarios of the mock host (`sessionStorage["rushbase.mock.scenario"]` or `?scenario=`). */
export type Scenario = "idle" | "running" | "first-run" | "unreachable";

const STAGES = ["probe", "hash", "preview", "shots", "audio", "embed", "vision", "classify"];

export function currentScenario(): Scenario {
  const fromUrl = new URLSearchParams(window.location.search).get("scenario");
  let stored: string | null = null;
  try {
    stored = window.sessionStorage.getItem("rushbase.mock.scenario");
  } catch {
    // Storage may be blocked; the default scenario applies.
  }
  const value = fromUrl ?? stored;
  return value === "running" || value === "first-run" || value === "unreachable" ? value : "idle";
}

function envelope<T>(command: string, data: T, ok = true): CliResult<T> {
  const doc: Envelope<T> = { schema_version: 1, command, ok, data };
  return { code: ok ? 0 : 1, envelope: doc, stderr: "" };
}

function failure<T>(command: string, error: string): CliResult<T> {
  return {
    code: 1,
    stderr: error,
    envelope: { schema_version: 1, command, ok: false, data: { error } },
  };
}

function overview(scenario: Scenario): Overview {
  const reachable = scenario !== "unreachable" && scenario !== "first-run";
  const stage = (jobs: number, files: number, hours: number) => ({
    pending_jobs: jobs,
    pending_files: files,
    pending_bytes: 0,
    pending_duration_ms: Math.round(hours * 3_600_000),
  });
  const backlog = reachable
    ? {
        stages: {
          probe: stage(0, 0, 0),
          hash: stage(0, 0, 0),
          preview: stage(142, 142, 11.4),
          shots: stage(142, 142, 11.4),
          audio: stage(118, 118, 9.8),
          embed: stage(142, 142, 11.4),
          vision: stage(142, 142, 11.4),
          classify: stage(142, 142, 11.4),
        },
        workers_online: 0,
        workers_total: 1,
      }
    : null;
  const seconds = (jobs: number, ms: number, parallel: number) =>
    Math.round((jobs * ms) / parallel / 1000);
  const estimateStages = {
    preview: {
      pending_jobs: 142,
      mean_job_ms: 24_000,
      seconds: seconds(142, 24_000, 4),
      source: "history" as const,
    },
    shots: {
      pending_jobs: 142,
      mean_job_ms: 18_000,
      seconds: seconds(142, 18_000, 4),
      source: "history" as const,
    },
    audio: {
      pending_jobs: 118,
      mean_job_ms: 9_000,
      seconds: seconds(118, 9_000, 1),
      source: "default" as const,
    },
    embed: {
      pending_jobs: 142,
      mean_job_ms: 7_000,
      seconds: seconds(142, 7_000, 1),
      source: "default" as const,
    },
    vision: {
      pending_jobs: 142,
      mean_job_ms: 22_000,
      seconds: seconds(142, 22_000, 1),
      source: "default" as const,
    },
    classify: {
      pending_jobs: 142,
      mean_job_ms: 1_500,
      seconds: seconds(142, 1_500, 1),
      source: "default" as const,
    },
  };
  const total = Object.values(estimateStages).reduce((sum, s) => sum + s.seconds, 0);
  const issues: Overview["issues"] =
    scenario === "first-run"
      ? [{ code: "no_server", message: "No server configured." }]
      : scenario === "unreachable"
        ? [{ code: "server_unreachable", message: "server unreachable: Connection refused" }]
        : [];
  return {
    server: {
      url: scenario === "first-run" ? null : "http://rushbase.lan:8080",
      reachable,
      error: scenario === "unreachable" ? "server unreachable: Connection refused" : null,
    },
    enrolled: scenario !== "first-run",
    worker:
      scenario === "first-run"
        ? null
        : { worker_id: "w-1", name: "gaming-pc", server_url: "http://rushbase.lan:8080" },
    backlog,
    totals: reachable ? { files: 142, hours: 11.4, jobs: 828 } : { files: 0, hours: 0, jobs: 0 },
    estimate: reachable ? { total_seconds: total, concurrency: 4, stages: estimateStages } : null,
    hardware: {
      gpu: "NVIDIA GeForce RTX 3070",
      vram_total_mb: 8192,
      vram_free_mb: 7276,
      backend: "cuda",
      profile: "gpu8",
      cpu: "AMD Ryzen 7 5800X",
      cores: 8,
    },
    models: {
      needed: ["qwen3.5-9b", "whisper-large-v3-turbo", "ced-small", "siglip2", "e5-base"],
      installed: ["qwen3.5-9b", "whisper-large-v3-turbo", "ced-small", "siglip2", "e5-base"],
      missing: [],
    },
    runnable_stages: STAGES,
    mount: reachable ? { root: "/mnt/rushbase", ok: true, error: null } : null,
    last_run: {
      finished_at: "2026-10-15T23:41:00+02:00",
      reason: "empty",
      completed: 312,
      failed: 0,
    },
    issues,
    ready: scenario === "idle" || scenario === "running",
  };
}

const TITLES = [
  "Hafen bei Nacht",
  "Boot im Morgenlicht",
  "Marktplatz Totale",
  "Interview Kapitän",
  "Drohne Küstenlinie",
  "Netze flicken",
  "Möwen am Kai",
  "Fischmarkt Detail",
  "Regen auf Kopfsteinpflaster",
  "Leuchtturm Dämmerung",
  "Kaffee am Pier",
  "Kran im Gegenlicht",
  "Werftarbeiter",
  "Segel setzen",
  "Wellen Zeitlupe",
  "Stadtrand Autobahn",
  "Vogelschwarm",
  "Strandspaziergang",
  "Fähre legt ab",
  "Container Stapel",
  "Sonnenuntergang Mole",
  "Kinder am Wasser",
  "Radfahrer Promenade",
  "Nebel über dem Fluss",
];
const HUES = [212, 188, 24, 340, 148, 262, 48, 8];

function poster(index: number): string {
  const hue = HUES[index % HUES.length] ?? 212;
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="480" height="270" viewBox="0 0 480 270">` +
    `<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${hue} 55% 38%)"/>` +
    `<stop offset="1" stop-color="hsl(${(hue + 40) % 360} 60% 62%)"/></linearGradient></defs>` +
    `<rect width="480" height="270" fill="url(#g)"/><circle cx="${120 + (index % 5) * 60}" cy="90" r="34" fill="hsl(${hue} 80% 85% / .55)"/>` +
    `<path d="M0 220 Q120 ${170 + (index % 4) * 12} 240 215 T480 205 V270 H0Z" fill="hsl(${hue} 45% 22% / .55)"/></svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

function summary(index: number): AssetSummary {
  const id = `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`;
  return {
    id,
    media_type: index % 11 === 5 ? "photo" : "video",
    filename: `A${String(Math.floor(index / 4) + 1).padStart(3, "0")}_C${String((index % 4) + 1).padStart(3, "0")}.mov`,
    duration_ms: index % 11 === 5 ? null : 8_000 + ((index * 7919) % 90_000),
    width: 3840,
    height: 2160,
    fps: index % 3 === 0 ? 23.976 : 25,
    codec: "prores",
    poster_url: `/api/v1/derived/${id}/poster.jpg`,
    summary: TITLES[index % TITLES.length] ?? null,
    review_state: index % 7 === 3 ? "pending" : "none",
  };
}

const ASSETS = Array.from({ length: 48 }, (_, i) => summary(i));

function detail(asset: AssetSummary, index: number): AssetDetail {
  const duration = asset.duration_ms ?? 0;
  const shotCount = asset.media_type === "photo" ? 0 : 3;
  const shots = Array.from({ length: shotCount }, (_, i) => ({
    id: `s${index}-${i}`,
    idx: i,
    start_ms: Math.round((duration / shotCount) * i),
    end_ms: Math.round((duration / shotCount) * (i + 1)),
    keyframes: [{ t_ms: 0, url: `/api/v1/derived/${asset.id}/kf/${i}.jpg`, position: "middle" }],
    ai: {
      description_short_de:
        ["Totale auf den Hafen", "Halbnah, Boot im Vordergrund", "Detail der Taue"][i] ?? null,
      description_short_en:
        ["Wide shot of the harbour", "Medium shot, boat in front", "Close-up of the ropes"][i] ??
        null,
      shot_size: ["wide", "medium", "close"][i] ?? null,
    },
  }));
  return {
    ...asset,
    files: [
      {
        role: "primary",
        rel_path: `Content/2026/${asset.filename}`,
        paths: { linux: `/mnt/rushbase/Content/2026/${asset.filename}` },
      },
    ],
    shots,
    proxy_url: `/api/v1/derived/${asset.id}/proxy.mp4`,
    local_path: `/mnt/rushbase/Content/2026/${asset.filename}`,
    local_exists: true,
    ai: {
      summary_short_de: asset.summary,
      summary_short_en: asset.summary,
      tags: [
        { value: "harbour", label_de: "Hafen", label_en: "Harbour", field_key: "location" },
        { value: "night", label_de: "Nacht", label_en: "Night", field_key: "time_of_day" },
        { value: "boat", label_de: "Boot", label_en: "Boat", field_key: "objects" },
      ],
    },
  };
}

const FACETS: Facet[] = [
  {
    key: "media_type",
    label_de: "Medium",
    label_en: "Media",
    kind: "enum",
    values: [
      { value: "video", count: 44 },
      { value: "photo", count: 4 },
    ],
  },
  {
    key: "camera_model",
    label_de: "Kamera",
    label_en: "Camera",
    kind: "enum",
    values: [
      { value: "FX6", count: 30 },
      { value: "A7S III", count: 14 },
      { value: "Mavic 3", count: 4 },
    ],
  },
  {
    key: "review_state",
    label_de: "Prüfung",
    label_en: "Review",
    kind: "enum",
    values: [
      { value: "pending", count: 7 },
      { value: "none", count: 41 },
    ],
  },
];

/** A bridge without a host: deterministic data and a simulated worker for demos and screenshots. */
export function createMockBridge(scenario: Scenario = currentScenario()): Bridge {
  let settings: Settings = {
    server_url: scenario === "first-run" ? null : "http://rushbase.lan:8080",
    library_root: scenario === "first-run" ? null : "/mnt/rushbase",
    cli_command: null,
    language: "de",
  };
  const basket: Basket = { count: 0, items: [] };
  const streamHandlers = new Set<(e: { name: StreamName; line: string }) => void>();
  const exitHandlers = new Set<(e: { name: StreamName; code: number }) => void>();
  const timers = new Map<StreamName, ReturnType<typeof setInterval>>();
  const running = new Set<StreamName>();

  const emit = (name: StreamName, data: Record<string, unknown>) => {
    const line = JSON.stringify({
      schema_version: 1,
      command: name === "process" ? "process" : "models pull",
      ok: true,
      data,
    });
    streamHandlers.forEach((h) => h({ name, line }));
  };

  function simulateProcess(): void {
    let step = 0;
    emit("process", { event: "started", stages: STAGES });
    timers.set(
      "process",
      setInterval(() => {
        step += 1;
        const stage =
          ["preview", "shots", "audio", "embed", "vision", "classify"][step % 6] ?? "preview";
        emit("process", { event: "job_started", job_id: `j${step}`, stage, asset_id: `a${step}` });
        emit("process", {
          event: "job_done",
          job_id: `j${step}`,
          stage,
          duration_ms: 4000 + (step % 5) * 900,
        });
      }, 700),
    );
  }

  function simulateModels(): void {
    let bytes = 0;
    const total = 5_900_000_000;
    emit("models", { event: "download_started", file: "Qwen3.5-9B-Q4_K_M.gguf", total });
    timers.set(
      "models",
      setInterval(() => {
        bytes += 620_000_000;
        if (bytes >= total) {
          emit("models", { event: "model_done", id: "qwen3.5-9b" });
          stopStream("models", 0);
          return;
        }
        emit("models", {
          event: "download_progress",
          file: "Qwen3.5-9B-Q4_K_M.gguf",
          bytes,
          total,
        });
      }, 500),
    );
  }

  function stopStream(name: StreamName, code: number): void {
    const timer = timers.get(name);
    if (timer) clearInterval(timer);
    timers.delete(name);
    if (!running.delete(name)) return;
    if (name === "process")
      emit("process", {
        event: "stopped",
        reason: "sigterm",
        completed: 12,
        failed: 0,
        released: 2,
      });
    exitHandlers.forEach((h) => h({ name, code }));
  }

  return {
    kind: "mock",
    async cli<T>(args: string[]): Promise<CliResult<T>> {
      const [command, sub] = args;
      const data = (value: unknown): CliResult<T> =>
        envelope(args.slice(0, 2).join(" "), value) as CliResult<T>;
      switch (command) {
        case "overview":
          return data(overview(scenario));
        case "hardware":
          return data(overview(scenario).hardware);
        case "version":
          return data({ version: "0.1.0" });
        case "doctor":
          return data({
            checks: [
              { name: "ffmpeg", status: "ok", detail: "ffmpeg 7.1" },
              { name: "exiftool", status: "ok", detail: "13.0" },
            ],
          });
        case "enroll":
          return data({ worker_id: "w-1", name: "gaming-pc", server_url: settings.server_url });
        case "login":
          return data({
            server_url: settings.server_url,
            username: "elias",
            token_file: "/tmp/user_token",
          });
        case "filters":
          return data({ facets: FACETS });
        case "browse": {
          const cursor = Number(args[args.indexOf("--cursor") + 1] || 0) || 0;
          const items = ASSETS.slice(cursor, cursor + 24);
          return data({
            items,
            next_cursor: cursor + 24 < ASSETS.length ? String(cursor + 24) : null,
            total: ASSETS.length,
          });
        }
        case "search": {
          const q = (args[1] ?? "").toLowerCase();
          const hits = ASSETS.filter(
            (a) => (a.summary ?? "").toLowerCase().includes(q) || q === "",
          ).slice(0, 12);
          return data({
            items: hits.map((asset) => ({ asset, shot: null, score: 0.5 })),
            candidates: hits.length,
            next_offset: null,
          });
        }
        case "asset": {
          const id = args[2] ?? "";
          const index = ASSETS.findIndex((a) => a.id === id);
          const asset = ASSETS[index];
          return asset
            ? data(detail(asset, index))
            : failure("asset get", "HTTP 404: asset not found");
        }
        case "select": {
          const id = args[2] ?? "";
          if (sub === "add" && id && !basket.items.some((i) => i.asset_id === id)) {
            const asset = ASSETS.find((a) => a.id === id);
            if (asset)
              basket.items.push({
                asset_id: id,
                filename: asset.filename,
                selected_shots: null,
                shots: 3,
                duration_ms: asset.duration_ms,
              });
          }
          if (sub === "remove") basket.items = basket.items.filter((i) => i.asset_id !== id);
          if (sub === "clear") basket.items = [];
          basket.count = basket.items.length;
          return data({ ...basket });
        }
        case "export":
          return data({
            format: sub,
            file: args[args.indexOf("--out") + 1],
            bytes: 4096,
            warnings: [],
          });
        case "resolve":
          if (sub === "status") {
            return envelope("resolve status", {
              installed: true,
              reachable: true,
              studio: true,
              version: "21.1.1.0003",
              project: "Hafen Doku",
              error: null,
            }) as CliResult<T>;
          }
          return data({
            project: "Hafen Doku",
            bin: "Rushbase",
            imported: basket.count,
            existing: 0,
            metadata_set: basket.count,
            markers_added: basket.count * 3,
            colored: 0,
            keys: [],
            errors: [],
            skipped: [],
          });
        case "models":
          return data({
            models: [],
            backends: ["llama"],
            profile: "gpu8",
            data_dir: "/home/me/.local/share/rushbase-worker",
          });
        default:
          return failure(command ?? "", `unsupported in the mock: ${command}`);
      }
    },
    async streamStart(name) {
      running.add(name);
      if (name === "process") simulateProcess();
      else simulateModels();
    },
    async streamStop(name) {
      stopStream(name, 0);
    },
    streamRunning: async (name) => running.has(name),
    onStream: async (handler) => {
      streamHandlers.add(handler);
      if (scenario === "running" && !running.has("process")) {
        running.add("process");
        simulateProcess();
      }
      return () => streamHandlers.delete(handler);
    },
    onStreamExit: async (handler) => {
      exitHandlers.add(handler);
      return () => exitHandlers.delete(handler);
    },
    onTrayAction: async () => () => undefined,
    getSettings: async () => settings,
    setSettings: async (next) => {
      settings = next;
    },
    gpuStats: async () => ({
      name: "NVIDIA GeForce RTX 3070",
      temperature_c: scenario === "running" ? 71 : 41,
      power_w: scenario === "running" ? 168 : 23,
      power_limit_w: 220,
      utilization_pct: scenario === "running" ? 96 : 2,
      memory_used_mb: scenario === "running" ? 7210 : 916,
      memory_total_mb: 8192,
    }),
    idleSeconds: async () => 0,
    platform: async () => ({
      os: "linux",
      wayland: false,
      appimage: false,
      tray: true,
      updater: false,
    }),
    dragCheck: async () => ({ ok: false, reason: "network_mount" }),
    startDrag: async () => undefined,
    revealInFolder: async () => undefined,
    copyText: async (text) => {
      await navigator.clipboard?.writeText(text).catch(() => undefined);
    },
    assetUrl: (path) => {
      const id = /([0-9a-f-]{36})/.exec(path)?.[1];
      const index = ASSETS.findIndex((a) => a.id === id);
      return poster(Math.max(index, 0));
    },
    pickDirectory: async () => "/mnt/rushbase",
    pickLibraryRoot: async () => {
      settings = { ...settings, library_root: "/mnt/rushbase" };
      return "/mnt/rushbase";
    },
    autostart: { get: async () => false, set: async () => undefined },
    quit: async () => undefined,
  };
}
