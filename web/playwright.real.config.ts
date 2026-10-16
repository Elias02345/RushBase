import { defineConfig, devices } from "@playwright/test";

// Real-stack run: started by `make e2e-real` (scripts/e2e-real.sh), which brings up a server on
// :18083 with a fresh database and a seeded library. Only the Vite dev server is started here.
const WEB_PORT = Number(process.env.RUSHBASE_E2E_WEB_PORT ?? 15175);
const SERVER_URL = process.env.RUSHBASE_E2E_SERVER_URL ?? "http://127.0.0.1:18083";

export default defineConfig({
  testDir: "./e2e-real",
  fullyParallel: false,
  workers: 1,
  timeout: 360_000,
  expect: { timeout: 30_000 },
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "e2e-real", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "pnpm exec vite",
    url: `http://127.0.0.1:${WEB_PORT}`,
    env: {
      RUSHBASE_DEV_SERVER_URL: SERVER_URL,
      RUSHBASE_DEV_WEB_PORT: String(WEB_PORT),
    },
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
