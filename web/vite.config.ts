/// <reference types="vitest/config" />
import { rmSync } from "node:fs";
import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const SERVER_URL = process.env.RUSHBASE_DEV_SERVER_URL ?? "http://127.0.0.1:18080";
const MOCK = process.env.VITE_MOCK === "1";

/** Keep the MSW service worker and the demo video out of real builds. */
function stripMockAssets(): Plugin {
  return {
    name: "rushbase-strip-mock-assets",
    apply: "build",
    closeBundle() {
      if (MOCK) return;
      const dist = resolve(import.meta.dirname, "dist");
      rmSync(resolve(dist, "mockServiceWorker.js"), { force: true });
      rmSync(resolve(dist, "mock"), { force: true, recursive: true });
    },
  };
}

export default defineConfig({
  plugins: [react(), stripMockAssets()],
  build: { target: "es2022", sourcemap: false },
  server: {
    host: "127.0.0.1",
    port: Number(process.env.RUSHBASE_DEV_WEB_PORT ?? 15173),
    strictPort: true,
    proxy: {
      // /api/health -> <server>/health; everything under /api/v1 is served as-is.
      "/api/health": {
        target: SERVER_URL,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      "/api/v1": { target: SERVER_URL, changeOrigin: true },
    },
  },
  preview: { host: "127.0.0.1", port: 15174, strictPort: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    exclude: ["e2e/**", "node_modules/**"],
  },
});
