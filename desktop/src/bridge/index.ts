import type { Bridge } from "./bridge";
import { createMockBridge } from "./mock";
import { createTauriBridge } from "./tauri";

export { cliData, CliError, derivedPath, type Bridge } from "./bridge";

/** The real host inside the app window, the mock in a plain browser or with `VITE_MOCK=1`. */
export function createBridge(): Bridge {
  const inTauri = "__TAURI_INTERNALS__" in window;
  if (import.meta.env.VITE_MOCK === "1" || !inTauri) return createMockBridge();
  return createTauriBridge();
}
