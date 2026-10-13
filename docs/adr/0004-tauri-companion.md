# ADR-0004: Tauri 2 companion app — drag-out, sidecars, updater, tray, autostart

- Status: Accepted
- Date: 2026-10-13

## Context
Versions as of 2026-10-13: `tauri` 2.12.3, `@tauri-apps/cli` 2.12.2, `tauri-plugin-updater` 2.13.3,
`tauri-plugin-shell` 2.5.0, `tauri-plugin-autostart` 2.7.0 (Rust ≥ 1.90), `tauri-plugin-drag` 2.1.1 /
`@crabnebula/tauri-plugin-drag` 2.1.0.

Drag-out (`drag-rs`) has open issues: crash when dragging from SMB/NAS paths (#72), crash on paths
> 260 chars (#76), GTK3/X11 drop reports success without delivering (#84), Windows `DoDragDrop`
hang (#94). Wayland behaviour UNVERIFIED. Linux tray icons do not emit click events.

## Decision
1. Pin exact versions; update as a set after CI passes.
2. Drag-out is wrapped so a failure never crashes the app and is gated by a startup self-test.
   Files on network mounts or with paths > 240 chars are not dragged directly; instead the app offers
   "Send to Resolve" (ADR-0002) or a local hard link/temp copy for small files. On Linux/Wayland or a
   failed self-test, drag-out is hidden and "Copy path" / "Reveal in file manager" are shown.
   Note: Resolve imports by path, so dragging the mount path is preferred where the plugin works.
3. The worker engine is shipped as a PyInstaller sidecar (`externalBin`, `name-<target-triple>`),
   with exact `args` patterns in the shell capability (never `args: true`).
4. Updater: one signing key in CI secrets (offline backup), `latest.json` on GitHub Releases, Linux via
   AppImage.
5. Tray menu contains every action (show, process/pause, quit) because Linux has no click events.
   Autostart is off by default and starts minimised to tray.

## Consequences
Linux gets a slightly degraded drag experience; known plugin bugs are fenced off by fallbacks.

## Sources
- https://github.com/crabnebula-dev/drag-rs (+ issues)
- https://v2.tauri.app/develop/sidecar
- https://v2.tauri.app/plugin/updater/
- https://v2.tauri.app/learn/system-tray/
- https://v2.tauri.app/plugin/autostart/
