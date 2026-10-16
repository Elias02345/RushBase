import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ThemeProvider, ToastProvider } from "@rushbase/ui";

import { App } from "./App";
import { cliData, derivedPath } from "./bridge";
import { BridgeProvider } from "./bridge/context";
import { createMockBridge, type Scenario } from "./bridge/mock";
import type { Overview } from "./bridge/types";
import de from "./locales/de.json";
import en from "./locales/en.json";
import { clockIn, formatDuration, parseEnrollCommand } from "./lib/format";
import { plannedJobs, processArgs, DEFAULT_OPTIONS } from "./state/options";
import { RunProvider } from "./state/run";
import { etaSeconds, initialRun, jobsPerMinute, runReducer } from "./state/runReducer";

function renderApp(scenario: Scenario) {
  const bridge = createMockBridge(scenario);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <ThemeProvider>
      <ToastProvider closeLabel="Schließen" regionLabel="Hinweise">
        <BridgeProvider bridge={bridge}>
          <QueryClientProvider client={client}>
            <RunProvider>
              <App />
            </RunProvider>
          </QueryClientProvider>
        </BridgeProvider>
      </ToastProvider>
    </ThemeProvider>,
  );
  return bridge;
}

describe("Processor", () => {
  it("shows the backlog, hardware and the estimate at a glance", async () => {
    renderApp("idle");
    expect(
      await screen.findByRole("heading", { level: 1, name: /142 Dateien/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/11,4 Std\. Material/)).toBeInTheDocument();
    expect(screen.getByText(/NVIDIA GeForce RTX 3070/)).toBeInTheDocument();
    expect(screen.getByText("Backend CUDA")).toBeInTheDocument();
    expect(screen.getByText(/Dauer etwa/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Verarbeiten" })).toBeEnabled();
  });

  it("starts with one click and reports live progress", async () => {
    const user = userEvent.setup();
    renderApp("idle");
    await user.click(await screen.findByRole("button", { name: "Verarbeiten" }));
    expect(await screen.findByRole("button", { name: /Pause \/ Stopp/ })).toBeInTheDocument();
    await waitFor(
      () =>
        expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
          /[1-9]\d* von 828 Aufträgen/,
        ),
      { timeout: 5_000 },
    );
    await user.click(screen.getByRole("button", { name: /Pause \/ Stopp/ }));
    expect(await screen.findByText(/Fertig/)).toBeInTheDocument();
  });

  it("guides a first run to the wizard", async () => {
    renderApp("first-run");
    expect(
      await screen.findByRole("heading", { name: "Mit dem Server verbinden" }),
    ).toBeInTheDocument();
  });

  it("explains an unreachable server with a retry", async () => {
    renderApp("unreachable");
    expect(await screen.findByText("Server nicht erreichbar")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Erneut versuchen" })).toBeInTheDocument();
  });
});

describe("Library", () => {
  it("lists assets, opens a detail and adds to the selection", async () => {
    const user = userEvent.setup();
    renderApp("idle");
    await user.click(await screen.findByRole("button", { name: "Bibliothek" }));
    const tiles = await screen.findAllByRole("button", { name: /A001_C001\.mov/ });
    await user.click(tiles[0] as HTMLElement);
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Szenen")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "In die Auswahl" }));
    await waitFor(() =>
      expect(
        within(dialog).getByRole("button", { name: "Auswahl aktualisieren" }),
      ).toBeInTheDocument(),
    );
    expect(within(dialog).getByText(/network|Netzlaufwerk/i)).toBeInTheDocument();
  });
});

describe("worker JSON helpers", () => {
  it("returns data or throws the CLI's message", async () => {
    const bridge = createMockBridge("idle");
    const overview = await cliData<Overview>(bridge, ["overview"]);
    expect(overview.totals.files).toBe(142);
    await expect(cliData(bridge, ["asset", "get", "missing"])).rejects.toThrow(/404/);
  });

  it("maps derived URLs to the local mount", () => {
    expect(derivedPath("/mnt/lib/", "/api/v1/derived/abc/kf/0%201.jpg")).toBe(
      "/mnt/lib/.rushbase/derived/abc/kf/0 1.jpg",
    );
    expect(derivedPath("D:\\lib", "/api/v1/derived/abc/poster.jpg")).toBe(
      "D:\\lib\\.rushbase\\derived\\abc\\poster.jpg",
    );
    expect(derivedPath("/mnt/lib", "https://evil/x.jpg")).toBeNull();
    expect(derivedPath("/mnt/lib", null)).toBeNull();
  });
});

describe("run model", () => {
  const event = (data: object, now: number) => ({
    type: "event" as const,
    event: data as never,
    now,
  });

  it("counts jobs, tracks the active stage and estimates the remaining time", () => {
    let state = runReducer(initialRun, { type: "begin", totalJobs: 10, now: 0 });
    for (let i = 0; i < 4; i++) {
      state = runReducer(
        state,
        event({ event: "job_started", job_id: `j${i}`, stage: "preview" }, i * 15_000),
      );
      state = runReducer(
        state,
        event(
          { event: "job_done", job_id: `j${i}`, stage: "preview", duration_ms: 1000 },
          i * 15_000 + 1,
        ),
      );
    }
    expect(state.phase).toBe("running");
    expect(state.done).toBe(4);
    expect(state.byStage).toEqual({ preview: 4 });
    expect(Object.keys(state.active)).toHaveLength(0);
    const now = 45_001;
    expect(jobsPerMinute(state, now)).toBeCloseTo(5.33, 1);
    expect(etaSeconds(state, now)).toBe(Math.round((6 / jobsPerMinute(state, now)!) * 60));
  });

  it("separates failures and releases, and finishes on exit", () => {
    let state = runReducer(initialRun, { type: "begin", totalJobs: 3, now: 0 });
    state = runReducer(
      state,
      event({ event: "job_failed", job_id: "a", stage: "shots", error: "x" }, 1),
    );
    state = runReducer(state, event({ event: "job_released", job_id: "b", stage: "shots" }, 2));
    state = runReducer(state, { type: "exit", code: 0, wanted: false });
    expect(state).toMatchObject({ phase: "finished", failed: 1, released: 1, done: 0 });
  });

  it("returns to waiting when a guard stopped a run the user still wants", () => {
    let state = runReducer(initialRun, { type: "begin", totalJobs: 3, now: 0 });
    state = runReducer(state, event({ event: "started" }, 1));
    state = runReducer(state, { type: "stopping", note: "guard_temp" });
    state = runReducer(state, { type: "exit", code: 0, wanted: true });
    expect(state).toMatchObject({ phase: "waiting", note: "guard_temp" });
  });
});

describe("options and formatting", () => {
  it("builds CLI arguments from the options", () => {
    const runnable = ["probe", "hash", "preview", "shots"];
    expect(processArgs(DEFAULT_OPTIONS, runnable)).toEqual(["--exit-when-empty"]);
    expect(
      processArgs({ ...DEFAULT_OPTIONS, stages: ["preview", "shots"], until: "06:30" }, runnable),
    ).toEqual(["--exit-when-empty", "--stages", "preview,shots", "--until", "06:30"]);
    expect(processArgs({ ...DEFAULT_OPTIONS, until: "late" }, runnable)).toEqual([
      "--exit-when-empty",
    ]);
  });

  it("plans jobs for the chosen stages only", () => {
    const backlog = {
      workers_online: 0,
      workers_total: 1,
      stages: {
        preview: { pending_jobs: 5, pending_files: 5, pending_bytes: 0, pending_duration_ms: 0 },
        vision: { pending_jobs: 7, pending_files: 7, pending_bytes: 0, pending_duration_ms: 0 },
      },
    };
    expect(plannedJobs(backlog, DEFAULT_OPTIONS, ["preview", "vision"])).toBe(12);
    expect(
      plannedJobs(backlog, { ...DEFAULT_OPTIONS, stages: ["vision"] }, ["preview", "vision"]),
    ).toBe(7);
    expect(plannedJobs(null, DEFAULT_OPTIONS, [])).toBe(0);
  });

  it("formats durations and clock times", () => {
    expect(formatDuration(45)).toBe("45 s");
    expect(formatDuration(600)).toBe("10 min");
    expect(formatDuration(8040)).toBe("2 h 14 min");
    expect(clockIn(3600, new Date(2026, 9, 16, 22, 30).getTime())).toBe("23:30");
  });

  it("reads a pasted enroll command or a bare token", () => {
    expect(
      parseEnrollCommand(
        "rushbase enroll --server http://srv:8080 --token abc-123 --name 'Mein PC'",
      ),
    ).toEqual({
      server: "http://srv:8080",
      token: "abc-123",
      name: "Mein PC",
    });
    expect(parseEnrollCommand("  tok123 ")).toEqual({ token: "tok123" });
  });
});

describe("i18n", () => {
  const flatten = (value: unknown, prefix = ""): string[] =>
    typeof value === "object" && value !== null
      ? Object.entries(value).flatMap(([key, child]) =>
          flatten(child, prefix ? `${prefix}.${key}` : key),
        )
      : [prefix];

  it("has identical keys in German and English", () => {
    expect(flatten(en).sort()).toEqual(flatten(de).sort());
  });

  it("defines every statically used key", () => {
    const keys = new Set(flatten(de).map((k) => k.replace(/_(one|other)$/, "")));
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) walk(path);
        else if (/\.tsx?$/.test(name) && !name.endsWith(".test.tsx")) files.push(path);
      }
    };
    walk(join(import.meta.dirname));
    const missing: string[] = [];
    for (const file of files) {
      for (const match of readFileSync(file, "utf8").matchAll(/\bt\(\s*"([a-zA-Z0-9_.]+)"/g)) {
        if (!keys.has(match[1] as string)) missing.push(`${file}: ${match[1]}`);
      }
    }
    expect(missing).toEqual([]);
  });
});
