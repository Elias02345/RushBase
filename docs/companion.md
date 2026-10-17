# Companion app

The companion exists in three equal forms that share one core, the Python worker package
(`rushbase_worker`):

| Form | For | Entry |
| --- | --- | --- |
| **Desktop app** (Tauri 2) | process footage on your GPU PC with one click, browse the library with local originals | `Rushbase` (AppImage, deb, rpm, MSI/NSIS, DMG) |
| **CLI** | scripts, servers, ssh | `rushbase ... --json` (`docs/cli-json.md`) |
| **Local MCP server** | agents on your PC (Claude Code, Claude Desktop) | `rushbase mcp serve` |

Every GUI action is a CLI command: the app only ever runs `rushbase ... --json` and streams the
JSON lines of `rushbase process --json`. There are no GUI-only features.

## Install

| OS | Package | Notes |
| --- | --- | --- |
| Linux | `Rushbase_<version>_amd64.AppImage`, `.deb`, `.rpm`, AUR `rushbase-bin` (`deploy/aur/PKGBUILD`) | needs `webkit2gtk-4.1`, `gtk3`, `libsoup3`; `libayatana-appindicator` for the tray (the app works without it) |
| Windows | `.msi` or `-setup.exe` (NSIS, per user) | the app is unsigned until the signing secrets exist (SmartScreen warning) |
| macOS | `.dmg` for Apple Silicon and for Intel | unsigned until the Apple secrets exist (right click, Open) |

The installers contain the worker as a sidecar (a frozen `rushbase`, about 140 MB). AI models and the
pinned llama.cpp / whisper.cpp builds are **not** inside: the app downloads them on demand with
SHA-256 verification (`rushbase models pull`).

Headless worker images (no desktop): `deploy/docker/worker.Dockerfile` with
`--build-arg VARIANT=cpu|cuda|vulkan|rocm`, see the header of that file.

## First run (wizard)

1. **Server**: address and enrollment token. Paste the `rushbase enroll --server ... --token ...`
   command from the web UI (Processing, "Add a PC") as is, or the bare token.
2. **Account**: user name and password once. The app creates a personal API token
   (`rushbase login`, stored mode 600 in the data directory); the password is never stored. Needed
   for search and the library, not for processing. Skippable.
3. **Library**: the folder under which the library is mounted on this PC. The app checks that
   `.rushbase/library.json` carries the server's library id (a wrong mount is refused).
4. **Hardware and models**: detected GPU, VRAM, backend and profile. "Download models" calls
   `rushbase models pull --json` with live progress; every file is hashed against the lock.
5. **System check**: `rushbase doctor`.

Everything can be repeated in Settings. Configuration the Rust side needs lives in
`<config dir>/app.rushbase.companion/settings.json`; the worker keeps its own state in its data
directory (`rushbase doctor` shows it).

## Processing

Opening the app shows the **Processor**: files and hours of material waiting, per stage, detected
GPU, backend, profile, installed models and a duration estimate. **Verarbeiten** starts
`rushbase process --exit-when-empty`; the hero turns into live progress (jobs done/total, current
stage, jobs per minute, ETA, event log). **Pause / Stopp** sends SIGTERM: in-flight jobs are handed
back to the queue. Quitting the app does the same (waits up to 25 s). Closing the window hides it
to the tray while a run is active (without a tray the app quits).

Options: stages to run, stop time (`--until HH:MM`), only while the PC is idle (X11:
`xprintidle`; otherwise logind, macOS `ioreg`, Windows `GetLastInputInfo`), maximum GPU temperature
(`nvidia-smi`; pauses, then resumes 8 degrees lower).

The estimate multiplies the queued jobs per stage with the mean job duration from your own runs
(`throughput.json` in the data directory, at least 3 samples) and falls back to defaults derived from
`docs/eval/2026-10-vlm-benchmark.md` (labelled "rough guide" in the UI). AI stages are assumed serial,
technical stages divide by the concurrency.

## Library

Search and filters come from the server (`rushbase search/browse/filters`); previews, posters and
keyframes are read from the **local mount** (`.rushbase/derived`) through Tauri's asset protocol,
scoped to the configured library root and read-only. The detail view plays the **original** from the
mount when the browser engine can decode it (H.264/HEVC/VP9/AV1 in mp4/mov/webm) and falls back to
the proxy otherwise or on error. Select clips or single scenes into the **selection**
(`rushbase select`), then export or send to Resolve.

Drag-out (tauri-plugin-drag) is only offered for local files with paths of at most 240 characters and
never on Wayland or network mounts (cifs, smb, nfs ...; ADR-0004). Otherwise the app shows "Copy
path" and "Show in file manager"; for network files use "Send to Resolve".

## DaVinci Resolve

Live push needs **DaVinci Resolve Studio 21.1+** (Free cannot be scripted from Python):
`Preferences > System > General > External scripting using: Local`. The bridge runs with Resolve's
own interpreter (`ResolvePython`, auto-detected) or a Python from `RUSHBASE_RESOLVE_PYTHON` with
`RUSHBASE_RESOLVE_SCRIPT_API` / `RUSHBASE_RESOLVE_SCRIPT_LIB` set for older versions.

```sh
rushbase resolve status --json                       # installed, version, reachable, open project
rushbase select add <asset-id> --shots 0,2
rushbase resolve send --yes --bin "Harbour" --markers --metadata
```

`send` imports into the **open project** (hence `--yes`), creates the bin, sets Description, Keywords,
Comments (`rushbase:<id>`), Shot, a clip colour by review state (pending orange, done green) and adds
markers at the selected shot starts. It is idempotent: clips are matched by file path or the
third-party metadata key `rushbase_asset_id`, markers by their `customData`. Every Resolve call's
result is checked; failures are reported, never swallowed. The metadata key names Resolve reports
are cached in `resolve_keys.json`.

**Self-test (never run automatically)**: `rushbase resolve selftest --yes [--project rushbase-test]`
generates a 2 s clip, creates or reuses a project whose name starts with `rushbase-test` (the bridge
refuses any other name), pushes, reads everything back and re-runs the push to prove idempotency,
then switches back to the previous project. Switching projects affects the open session: save your
work first.

### Cross-platform projects

Resolve stores absolute media paths. For a project shared between machines either give every machine
the **same mount path** (for example `/mnt/rushbase` on Linux and macOS, a mapped drive letter on
Windows that Resolve's "Mapped Mount" setting translates) or use Resolve's *Mapped Mount* setting
(Project Settings > General Options). Rushbase stores paths relative to the library root, and the
server lists every machine's root (Administration > Library) so `rushbase paths resolve <id>` shows
the right path per OS.

### Exports without scripting (also for the free edition)

```sh
rushbase export fcpxml --out cut.fcpxml    # FCPXML 1.10, selected shots as clips on one timeline
rushbase export edl    --out cut.edl       # CMX 3600, reel names of at most 8 characters
rushbase export csv    --out meta.csv [--columns "File Name,Description,Keywords"]
```

Times are exact rationals (`1001/30000s` for 29.97, `1001/24000s` for 23.976). Source timecode comes
from the file's start timecode. EDL timecode is non-drop-frame. The CSV default columns are File
Name, Clip Directory, Description, Keywords, Comments, Scene, Shot, Take, Good Take (Resolve's
"Import Metadata from File"; Scene, Take and Good Take stay empty because Rushbase has no such
data). The FCPXML is covered by structural tests, not validated against Apple's DTD (not freely
distributable).

## Local MCP server for agents

```sh
rushbase mcp serve --stdio                                        # default for agent clients
rushbase mcp serve --http 127.0.0.1:18090 --token-file ~/.rushbase-mcp-token   # streamable HTTP
```

Tools: `search_media`, `get_asset`, `get_keyframes` (images read from the local mount),
`list_filters`, `processing_status`, `processing_start`, `processing_stop`, `hardware`,
`selection_list/add/remove/clear`, `resolve_path`, `resolve_status`, `send_to_resolve` (needs
`confirm: true`), `export_fcpxml/edl/csv` (written into `<data dir>/exports`). Names and shapes of
`search_media`, `get_asset`, `get_keyframes` and `processing_status` follow the server MCP. There is no
tool that deletes or moves anything. The HTTP mode requires a bearer token (at least 16 characters).

Claude Code (`.mcp.json` in your project, or `claude mcp add rushbase -- rushbase mcp serve --stdio`):

```json
{
  "mcpServers": {
    "rushbase-local": {
      "command": "rushbase",
      "args": ["mcp", "serve", "--stdio"],
      "env": {
        "RUSHBASE_SERVER_URL": "http://rushbase.lan:8080",
        "RUSHBASE_LIBRARY_ROOT": "/mnt/rushbase"
      }
    }
  }
}
```

Run `rushbase login --server ... --username ...` once so the tools may search.

## Updates

The updater plugin is compiled in only when `RUSHBASE_UPDATER_PUBKEY` is set at build time; the
default builds have no updater (Settings shows "Updates off"). To enable it:

```sh
pnpm --filter @rushbase/desktop tauri signer generate -w ~/.tauri/rushbase.key
# repository variable RUSHBASE_UPDATER_PUBKEY = contents of the .pub file
# secrets TAURI_SIGNING_PRIVATE_KEY (+ _PASSWORD); keep an offline backup of the private key
```

`.github/workflows/desktop.yml` then signs the bundles and uploads `latest.json` to the GitHub
release (`https://github.com/Elias02345/Rushbase/releases/latest/download/latest.json`). The key in
`tauri.conf.json` is a placeholder that is never used.

## Development

```sh
scripts/with-env.sh pnpm --filter @rushbase/desktop dev        # in a browser: mock host, no Tauri needed
#   http://127.0.0.1:15177/?scenario=running   (idle | running | first-run | unreachable)
RUSHBASE_CLI="uv run rushbase" pnpm --filter @rushbase/desktop tauri dev   # real app, worker from source
desktop/scripts/build-sidecar.sh                                # frozen sidecar for this OS
pnpm --filter @rushbase/desktop tauri build --bundles appimage,deb
pnpm --filter @rushbase/desktop screenshots                     # docs/screenshots/desktop-*.png
```

The worker command resolves in this order: `RUSHBASE_CLI`, `cli_command` in `settings.json`
(edit the file by hand; the webview cannot read or set it), the bundled sidecar next to the
executable, `rushbase` from `PATH`. The webview cannot spawn processes: Rust only runs an
allow-list of sub-commands **and flags per sub-command**
(`desktop/src-tauri/src/sidecar.rs`) and a fixed set of options for `process` and `models pull`;
the capability file grants no shell or filesystem access. The library root is chosen in a native
folder dialog run by Rust (the webview never supplies the path, which also scopes the read-only
asset protocol), and the server URL must be `http://` or `https://`. Release builds with the
updater (`RUSHBASE_UPDATER_PUBKEY`) fail while the key is empty or the placeholder.

## Troubleshooting

- **Blank window on Wayland with NVIDIA**: webkit2gtk's DMA-BUF renderer. The app sets
  `WEBKIT_DISABLE_DMABUF_RENDERER=1` itself when it detects Wayland plus the proprietary driver; if
  you still see a blank window set it (and `WEBKIT_DISABLE_COMPOSITING_MODE=1`) in the environment or
  use the AUR launcher `rushbase-desktop`.
- **AppImage does not start**: `APPIMAGE_EXTRACT_AND_RUN=1 ./Rushbase.AppImage` (no FUSE needed).
- **No tray icon**: install `libayatana-appindicator` and a tray host (GNOME needs the AppIndicator
  extension). Without it closing the window quits the app.
- **"Library does not match the server"**: the folder must contain `.rushbase/library.json` of the
  server's library; check the share and the mount path.
- **Search says sign-in required**: run the wizard's account step or `rushbase login`.
- **Resolve not offered**: Studio edition, scripting set to Local, Resolve running, then
  `rushbase resolve status`.
- **Logs**: `<app log dir>/desktop.log` lists every CLI call (set `RUSHBASE_DESKTOP_LOG` to move it).
