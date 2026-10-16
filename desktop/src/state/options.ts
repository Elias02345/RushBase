import type { Backlog } from "../bridge/types";

export const ALL_STAGES = [
  "probe",
  "hash",
  "preview",
  "shots",
  "audio",
  "embed",
  "vision",
  "classify",
] as const;
export const STORAGE_KEY = "rushbase.desktop.run";
export const IDLE_CHECK_MS = 10_000;
export const TEMP_CHECK_MS = 5_000;
export const TEMP_HYSTERESIS = 8;
export const ACTIVITY_SECONDS = 5;

/** What the user can tune before pressing "Verarbeiten". */
export interface RunOptions {
  /** Empty = every stage this PC can run. */
  stages: string[];
  /** `HH:MM` local time, empty = no stop time. */
  until: string;
  idleOnly: boolean;
  idleMinutes: number;
  /** Pause above this GPU temperature in °C (0 = off). */
  maxTemp: number;
}

export const DEFAULT_OPTIONS: RunOptions = {
  stages: [],
  until: "",
  idleOnly: false,
  idleMinutes: 5,
  maxTemp: 0,
};

export function loadOptions(): RunOptions {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...DEFAULT_OPTIONS, ...(JSON.parse(raw) as Partial<RunOptions>) };
  } catch {
    // Storage may be unavailable; defaults apply.
  }
  return DEFAULT_OPTIONS;
}

/** CLI options for `rushbase process` from the UI options. */
export function processArgs(options: RunOptions, runnable: string[]): string[] {
  const args = ["--exit-when-empty"];
  const chosen = options.stages.filter((s) => runnable.includes(s));
  if (chosen.length > 0 && chosen.length < runnable.length) args.push("--stages", chosen.join(","));
  if (/^\d{1,2}:\d{2}$/.test(options.until)) args.push("--until", options.until);
  return args;
}

/** Number of queued jobs for the chosen stages. */
export function plannedJobs(
  backlog: Backlog | null,
  options: RunOptions,
  runnable: string[],
): number {
  if (!backlog) return 0;
  const chosen = options.stages.length > 0 ? options.stages : runnable;
  return Object.entries(backlog.stages)
    .filter(([stage]) => chosen.includes(stage))
    .reduce((sum, [, value]) => sum + value.pending_jobs, 0);
}
