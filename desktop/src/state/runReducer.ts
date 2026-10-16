import type { ProcessEvent } from "../bridge/types";

export type Phase = "idle" | "waiting" | "starting" | "running" | "stopping" | "finished";
export type Note = "guard_temp" | "not_idle" | null;

export interface LogEntry {
  at: number;
  event: ProcessEvent;
}

export interface RunState {
  phase: Phase;
  startedAt: number | null;
  totalJobs: number;
  done: number;
  failed: number;
  released: number;
  byStage: Record<string, number>;
  active: Record<string, string>;
  log: LogEntry[];
  doneAt: number[];
  reason: string | null;
  exitCode: number | null;
  note: Note;
}

export const initialRun: RunState = {
  phase: "idle",
  startedAt: null,
  totalJobs: 0,
  done: 0,
  failed: 0,
  released: 0,
  byStage: {},
  active: {},
  log: [],
  doneAt: [],
  reason: null,
  exitCode: null,
  note: null,
};

export type RunAction =
  | { type: "reset" }
  | { type: "wait"; note: Note }
  | { type: "begin"; totalJobs: number; now: number }
  | { type: "stopping"; note?: Note }
  | { type: "event"; event: ProcessEvent; now: number }
  | { type: "exit"; code: number; wanted: boolean };

const MAX_LOG = 60;

function without(record: Record<string, string>, key: string | undefined): Record<string, string> {
  return Object.fromEntries(Object.entries(record).filter(([k]) => k !== key));
}
const WINDOW_MS = 180_000;

/** Fold the JSON lines of `rushbase process --json` into the progress model of the UI. */
export function runReducer(state: RunState, action: RunAction): RunState {
  switch (action.type) {
    case "reset":
      return initialRun;
    case "wait":
      return { ...initialRun, phase: "waiting", note: action.note };
    case "begin":
      return {
        ...initialRun,
        phase: "starting",
        startedAt: action.now,
        totalJobs: action.totalJobs,
      };
    case "stopping":
      return state.phase === "running" || state.phase === "starting"
        ? { ...state, phase: "stopping", note: action.note ?? state.note }
        : state;
    case "exit": {
      if (state.phase === "idle") return state;
      if (action.wanted) return { ...state, phase: "waiting", exitCode: action.code, active: {} };
      return { ...state, phase: "finished", exitCode: action.code, active: {} };
    }
    case "event": {
      const { event, now } = action;
      const log = [...state.log, { at: now, event }].slice(-MAX_LOG);
      const next: RunState = {
        ...state,
        log,
        phase: state.phase === "starting" || state.phase === "waiting" ? "running" : state.phase,
      };
      switch (event.event) {
        case "job_started":
          if (event.job_id && event.stage)
            next.active = { ...state.active, [event.job_id]: event.stage };
          break;
        case "job_done": {
          next.active = without(state.active, event.job_id);
          next.done = state.done + 1;
          const stage = event.stage ?? "?";
          next.byStage = { ...state.byStage, [stage]: (state.byStage[stage] ?? 0) + 1 };
          next.doneAt = [...state.doneAt.filter((t) => now - t < WINDOW_MS), now];
          break;
        }
        case "job_failed": {
          next.active = without(state.active, event.job_id);
          next.failed = state.failed + 1;
          break;
        }
        case "job_released": {
          next.active = without(state.active, event.job_id);
          next.released = state.released + 1;
          break;
        }
        case "stopped":
          next.reason = event.reason ?? null;
          break;
      }
      return next;
    }
  }
}

/** Completed jobs per minute over the recent window (null until there are two samples). */
export function jobsPerMinute(state: RunState, now: number): number | null {
  const recent = state.doneAt.filter((t) => now - t < WINDOW_MS);
  if (recent.length < 2) return null;
  const span = Math.max(now - Math.min(...recent), 10_000);
  return (recent.length / span) * 60_000;
}

/** Seconds until the planned jobs are done at the recent speed (null while unknown). */
export function etaSeconds(state: RunState, now: number): number | null {
  const rate = jobsPerMinute(state, now);
  const remaining = state.totalJobs - state.done - state.failed;
  if (!rate || remaining <= 0) return null;
  return Math.round((remaining / rate) * 60);
}

/** Stage currently worked on (the one with the most active jobs). */
export function currentStage(state: RunState): string | null {
  const counts: Record<string, number> = {};
  for (const stage of Object.values(state.active)) counts[stage] = (counts[stage] ?? 0) + 1;
  return Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
}
