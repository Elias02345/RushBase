import { setupWorker } from "msw/browser";

import { handlers } from "./handlers";

/** Start the in-browser mock server (service worker) used by `VITE_MOCK=1`. */
export async function startMockServer(): Promise<void> {
  const worker = setupWorker(...handlers);
  await worker.start({ onUnhandledFrame: "bypass", quiet: true });
}
