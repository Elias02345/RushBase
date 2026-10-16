import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { useBridge } from "../bridge/context";
import type { Backlog, ProcessEvent } from "../bridge/types";
import {
  ACTIVITY_SECONDS,
  IDLE_CHECK_MS,
  loadOptions,
  plannedJobs,
  processArgs,
  STORAGE_KEY,
  TEMP_CHECK_MS,
  TEMP_HYSTERESIS,
  type RunOptions,
} from "./options";
import { initialRun, runReducer, type RunState } from "./runReducer";

interface RunContextValue {
  state: RunState;
  options: RunOptions;
  setOptions: (options: RunOptions) => void;
  start: (backlog: Backlog | null, runnable: string[]) => Promise<void>;
  stop: () => Promise<void>;
  dismiss: () => void;
  error: string | null;
}

const RunContext = createContext<RunContextValue | null>(null);

/** Owns the local `process` run: starting, streaming events, guards (idle, temperature) and stop. */
export function RunProvider({ children }: { children: ReactNode }) {
  const bridge = useBridge();
  const client = useQueryClient();
  const [state, dispatch] = useReducer(runReducer, initialRun);
  const [options, setOptionsState] = useState<RunOptions>(loadOptions);
  const [error, setError] = useState<string | null>(null);
  const wanted = useRef(false);
  const latest = useRef({ backlog: null as Backlog | null, runnable: [] as string[] });
  const stateRef = useRef(state);
  const optionsRef = useRef(options);
  useEffect(() => {
    stateRef.current = state;
    optionsRef.current = options;
  });

  const setOptions = useCallback((next: RunOptions) => {
    setOptionsState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      // Remembering the options is a convenience only.
    }
  }, []);

  const launch = useCallback(async () => {
    const { backlog, runnable } = latest.current;
    dispatch({
      type: "begin",
      totalJobs: plannedJobs(backlog, optionsRef.current, runnable),
      now: Date.now(),
    });
    setError(null);
    try {
      await bridge.streamStart("process", processArgs(optionsRef.current, runnable));
    } catch (cause) {
      setError(String(cause));
      wanted.current = false;
      dispatch({ type: "exit", code: -1, wanted: false });
    }
  }, [bridge]);

  const start = useCallback(
    async (backlog: Backlog | null, runnable: string[]) => {
      latest.current = { backlog, runnable };
      wanted.current = true;
      if (optionsRef.current.idleOnly) {
        dispatch({ type: "wait", note: "not_idle" });
        return;
      }
      await launch();
    },
    [launch],
  );

  const stop = useCallback(async () => {
    wanted.current = false;
    if (stateRef.current.phase === "waiting") {
      dispatch({ type: "exit", code: 0, wanted: false });
      return;
    }
    dispatch({ type: "stopping" });
    await bridge.streamStop("process");
  }, [bridge]);

  const dismiss = useCallback(() => dispatch({ type: "reset" }), []);

  useEffect(() => {
    const unlisten: (() => void)[] = [];
    let alive = true;
    void bridge
      .onStream(({ name, line }) => {
        if (name !== "process") return;
        try {
          const document = JSON.parse(line) as { data?: ProcessEvent };
          if (document.data?.event)
            dispatch({ type: "event", event: document.data, now: Date.now() });
        } catch {
          // Non-JSON output is diagnostics only.
        }
      })
      .then((off) => (alive ? unlisten.push(off) : off()));
    void bridge
      .onStreamExit(({ name, code }) => {
        if (name !== "process") return;
        dispatch({ type: "exit", code, wanted: wanted.current });
        void client.invalidateQueries({ queryKey: ["overview"] });
      })
      .then((off) => (alive ? unlisten.push(off) : off()));
    void bridge
      .onTrayAction((action) => {
        if (action === "stop") void stop();
        else if (stateRef.current.phase !== "running")
          void start(latest.current.backlog, latest.current.runnable);
      })
      .then((off) => (alive ? unlisten.push(off) : off()));
    return () => {
      alive = false;
      unlisten.forEach((off) => off());
    };
  }, [bridge, client, start, stop]);

  // Idle-only: start after the PC has been idle, stop gracefully when the user comes back.
  useEffect(() => {
    if (!options.idleOnly) return;
    const timer = setInterval(() => {
      if (!wanted.current) return;
      void bridge.idleSeconds().then((idle) => {
        if (idle === null) return;
        const phase = stateRef.current.phase;
        if (phase === "waiting" && idle >= optionsRef.current.idleMinutes * 60) void launch();
        else if (phase === "running" && idle < ACTIVITY_SECONDS) {
          dispatch({ type: "stopping", note: "not_idle" });
          void bridge.streamStop("process");
        }
      });
    }, IDLE_CHECK_MS);
    return () => clearInterval(timer);
  }, [bridge, options.idleOnly, launch]);

  // Temperature guard: pause above the limit, resume once the GPU has cooled down.
  useEffect(() => {
    if (options.maxTemp <= 0) return;
    const timer = setInterval(() => {
      void bridge.gpuStats().then((stats) => {
        const temperature = stats?.temperature_c;
        if (temperature == null || !wanted.current) return;
        const phase = stateRef.current.phase;
        if (phase === "running" && temperature >= optionsRef.current.maxTemp) {
          dispatch({ type: "stopping", note: "guard_temp" });
          void bridge.streamStop("process");
        } else if (
          phase === "waiting" &&
          temperature <= optionsRef.current.maxTemp - TEMP_HYSTERESIS
        ) {
          void launch();
        }
      });
    }, TEMP_CHECK_MS);
    return () => clearInterval(timer);
  }, [bridge, options.maxTemp, launch]);

  const value = useMemo(
    () => ({ state, options, setOptions, start, stop, dismiss, error }),
    [state, options, setOptions, start, stop, dismiss, error],
  );
  return <RunContext.Provider value={value}>{children}</RunContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useRun(): RunContextValue {
  const value = useContext(RunContext);
  if (!value) throw new Error("RunProvider is missing");
  return value;
}
