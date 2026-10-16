/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The Tauri dev window loads this server (devUrl in tauri.conf.json), so the port is fixed.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  build: { target: "es2022", sourcemap: false },
  server: {
    host: "127.0.0.1",
    port: Number(process.env.RUSHBASE_DESKTOP_DEV_PORT ?? 15177),
    strictPort: true,
  },
  preview: { host: "127.0.0.1", port: 15178, strictPort: true },
  test: {
    environment: "jsdom",
    globals: true,
    testTimeout: 20_000,
    setupFiles: ["./src/test-setup.ts"],
    exclude: ["e2e/**", "node_modules/**", "dist*/**"],
  },
});
