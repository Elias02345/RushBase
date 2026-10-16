import { startDrag } from "@crabnebula/tauri-plugin-drag";
import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { disable, enable, isEnabled } from "@tauri-apps/plugin-autostart";
import { open } from "@tauri-apps/plugin-dialog";
import { revealItemInDir } from "@tauri-apps/plugin-opener";

import { parseEnvelope, type Bridge } from "./bridge";
import type { CliResult, DragCheck, GpuStats, PlatformInfo, Settings, StreamName } from "./types";

/** 1x1 transparent PNG: the plugin requires an icon but the OS draws the file icon anyway. */
const DRAG_ICON =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAHfUsLwAAAABJRU5ErkJggg==";

interface RawOutput {
  code: number;
  stdout: string;
  stderr: string;
}

/** Bridge to the Rust side of the app (Tauri commands and plugins). */
export function createTauriBridge(): Bridge {
  return {
    kind: "tauri",
    async cli<T>(args: string[], stdin?: string): Promise<CliResult<T>> {
      const out = await invoke<RawOutput>("cli", { args, stdin: stdin ?? null });
      return { code: out.code, stderr: out.stderr, envelope: parseEnvelope<T>(out.stdout) };
    },
    async streamStart(name, options) {
      await invoke("stream_start", { name, options });
    },
    async streamStop(name) {
      await invoke("stream_stop", { name });
    },
    streamRunning: (name) => invoke<boolean>("stream_running", { name }),
    onStream: (handler) =>
      listen<{ name: StreamName; line: string }>("stream-event", (e) => handler(e.payload)),
    onStreamExit: (handler) =>
      listen<{ name: StreamName; code: number }>("stream-exit", (e) => handler(e.payload)),
    onTrayAction: (handler) =>
      listen<string>("tray-action", (e) => handler(e.payload === "start" ? "start" : "stop")),
    getSettings: () => invoke<Settings>("get_settings"),
    setSettings: (settings) => invoke("set_settings", { settings }),
    gpuStats: () => invoke<GpuStats | null>("gpu_stats"),
    idleSeconds: () => invoke<number | null>("idle_seconds"),
    platform: () => invoke<PlatformInfo>("platform_info"),
    dragCheck: (path) => invoke<DragCheck>("drag_check", { path }),
    async startDrag(paths) {
      await startDrag({ item: paths, icon: DRAG_ICON });
    },
    async revealInFolder(path) {
      await revealItemInDir(path);
    },
    async copyText(text) {
      await navigator.clipboard.writeText(text);
    },
    assetUrl: (path) => convertFileSrc(path),
    async pickDirectory() {
      const chosen = await open({ directory: true, multiple: false });
      return typeof chosen === "string" ? chosen : null;
    },
    autostart: {
      get: () => isEnabled(),
      set: (enabled) => (enabled ? enable() : disable()),
    },
    quit: () => invoke("quit_app"),
  };
}
