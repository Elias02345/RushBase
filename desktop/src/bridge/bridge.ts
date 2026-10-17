import type { CliResult, DragCheck, GpuStats, PlatformInfo, Settings, StreamName } from "./types";

/** Everything the UI needs from the host: the worker CLI, process streams and OS probes. */
export interface Bridge {
  readonly kind: "tauri" | "mock";
  /** Run an allow-listed CLI sub-command (`--json` is appended by the caller). */
  cli<T>(args: string[], stdin?: string): Promise<CliResult<T>>;
  streamStart(name: StreamName, options: string[]): Promise<void>;
  streamStop(name: StreamName): Promise<void>;
  streamRunning(name: StreamName): Promise<boolean>;
  onStream(handler: (event: { name: StreamName; line: string }) => void): Promise<() => void>;
  onStreamExit(handler: (event: { name: StreamName; code: number }) => void): Promise<() => void>;
  onTrayAction(handler: (action: "start" | "stop") => void): Promise<() => void>;
  getSettings(): Promise<Settings>;
  setSettings(settings: Settings): Promise<void>;
  gpuStats(): Promise<GpuStats | null>;
  idleSeconds(): Promise<number | null>;
  platform(): Promise<PlatformInfo>;
  dragCheck(path: string): Promise<DragCheck>;
  startDrag(paths: string[]): Promise<void>;
  revealInFolder(path: string): Promise<void>;
  copyText(text: string): Promise<void>;
  /** URL the webview can load for a local file below the library root (read-only). */
  assetUrl(path: string): string;
  pickDirectory(): Promise<string | null>;
  /** Native folder dialog run by the Rust side; stores and returns the chosen library root. */
  pickLibraryRoot(): Promise<string | null>;
  autostart: { get(): Promise<boolean>; set(enabled: boolean): Promise<void> };
  quit(): Promise<void>;
}

/** Thrown by [`cliData`] when a command fails; carries the CLI's own message. */
export class CliError extends Error {
  constructor(
    message: string,
    readonly code: number,
  ) {
    super(message);
    this.name = "CliError";
  }
}

/** Run a command and return its `data`, or throw a [`CliError`] with the CLI's message. */
export async function cliData<T>(bridge: Bridge, args: string[], stdin?: string): Promise<T> {
  const result = await bridge.cli<T>([...args, "--json"], stdin);
  const envelope = result.envelope;
  if (result.code === 0 && envelope?.ok) return envelope.data as T;
  const message =
    envelope && !envelope.ok && "error" in (envelope.data as object)
      ? String((envelope.data as { error: string }).error)
      : result.stderr.trim().split("\n").pop() || `exit code ${result.code}`;
  throw new CliError(message, result.code);
}

/** Parse the last JSON line of CLI stdout into an envelope (null when there is none). */
export function parseEnvelope<T>(stdout: string): CliResult<T>["envelope"] {
  const line = stdout.trim().split("\n").pop();
  if (!line) return null;
  try {
    return JSON.parse(line) as CliResult<T>["envelope"];
  } catch {
    return null;
  }
}

/** Library-relative derived file URL (`/api/v1/derived/<id>/<sub>`) to a path below `root`. */
export function derivedPath(root: string, url: string | null): string | null {
  const match = url?.match(/^\/api\/v1\/derived\/([^/]+)\/(.+)$/);
  if (!match) return null;
  const [, id, sub] = match;
  const separator = root.includes("\\") && !root.includes("/") ? "\\" : "/";
  return [
    root.replace(/[\\/]+$/, ""),
    ".rushbase",
    "derived",
    id,
    decodeURIComponent(sub ?? ""),
  ].join(separator);
}
