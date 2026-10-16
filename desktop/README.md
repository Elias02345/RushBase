# desktop

The Rushbase companion app (Tauri 2): **Processor** (default) and **Library** views, the worker CLI
as a bundled sidecar. Guide, install, Resolve setup, MCP and troubleshooting: `docs/companion.md`.

```sh
scripts/with-env.sh pnpm --filter @rushbase/desktop dev          # frontend in a browser (mock host)
RUSHBASE_CLI="uv run rushbase" scripts/with-env.sh pnpm --filter @rushbase/desktop tauri dev
scripts/with-env.sh desktop/scripts/build-sidecar.sh             # PyInstaller sidecar for this OS
scripts/with-env.sh pnpm --filter @rushbase/desktop tauri build --bundles appimage,deb
```

Layout: `src/` React frontend (`bridge/` is the only place that talks to the host, with a Tauri and a
mock implementation), `src-tauri/` Rust (`sidecar.rs` runs the CLI, `streams.rs` streams `process`
and `models pull`, `system.rs` GPU/idle/drag probes, `tray.rs`).
