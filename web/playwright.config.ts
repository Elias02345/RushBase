import { defineConfig, devices } from "@playwright/test";

const PORT = 15174;

// Browsers install into PLAYWRIGHT_BROWSERS_PATH (set by scripts/dev-env.sh locally, by the CI
// cache step in CI); the app is served from a production build with the in-browser mock API.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    serviceWorkers: "allow",
  },
  projects: [
    { name: "e2e", testIgnore: /screenshots\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
    { name: "screenshots", testMatch: /screenshots\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    command: `pnpm exec vite build && pnpm exec vite preview --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}`,
    env: { VITE_MOCK: "1" },
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
