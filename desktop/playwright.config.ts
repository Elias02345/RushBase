import { defineConfig, devices } from "@playwright/test";

const PORT = 15179;

// The frontend runs against the mock bridge (VITE_MOCK=1), so no Tauri host is needed to take
// screenshots or to run the view tests in a plain browser.
export default defineConfig({
  testDir: "./e2e",
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    ...devices["Desktop Chrome"],
    viewport: { width: 1180, height: 800 },
  },
  webServer: {
    command: `pnpm exec vite build --outDir dist-mock && pnpm exec vite preview --outDir dist-mock --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}`,
    env: { VITE_MOCK: "1" },
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
