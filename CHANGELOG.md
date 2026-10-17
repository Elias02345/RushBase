# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed (repository)

- README rewritten with logo, screenshots, architecture diagram and a verified quick start; issue
  and pull request templates, Code of Conduct, release-notes categories; Dependabot now groups
  minor/patch updates per ecosystem, adds Cargo and ignores pinned or major-migration packages.

### Added (worker)

- `rushbase models pull` installs the pinned, CI-built whisper.cpp release build for the detected
  hardware (CUDA, Vulkan, CPU, Metal) with SHA-256 verification; the upstream CPU build is only a
  fallback. `update-lock.py binaries` fills `whisper.cpp.platforms` from the release. On Windows the
  backend libraries are put on `PATH`.
### Added (final gaps)

- "Used in project": `rushbase resolve sync-usage` reads the timeline usage of the Rushbase clips
  in the open Resolve project (read-only) and reports it with the personal token or the worker
  key; table `asset_usage` (migration 0009), facet and filter "Used in project" in the library,
  a _Usage_ tab in the asset detail with manual marks.
- Reprocess by model: administrators queue a stage again for every asset whose latest result has
  an older or different model version, model or profile (dry-run count first), in _Processing ->
  Reprocess_ with the current models per stage; the MCP tool `reprocess` takes a `model` selector
  and `dry_run`.
- Dashboard: storage growth per month and media type, a coverage heatmap of any two fields (cells
  open the filtered library, weakest cells listed) and job throughput per hour for the last seven
  days; all grouped single-pass queries behind the TTL cache.
- Audit-log viewer (_Administration -> Audit log_): filter by actor, action, target and time,
  keyset pagination, detail drawer.
- The asset detail lists the needs items the asset is a candidate for and lets editors confirm or
  reject inline.
- Personal API tokens: optional expiry (never, 30, 90, 365 days), enforced on every request and
  shown in the list; new _Administration -> API tokens_ tab.

### Added (spec gaps)

- Delete to trash: administrators move a filed asset or duplicate (card folders whole, with
  sidecars) to `.rushbase/trash/<asset-id>/` from the web UI after a confirmation, restore it
  while the original path is free, and set the retention period (default 30 days). The
  maintenance job deletes expired items for good: the only code path that removes media, audited
  and tested. Not available through MCP, API tokens or the worker (migration 0008).
- Privacy: consent, proof reference and "person identifiable" in the asset detail and in bulk
  edit; "Privacy" page that finds and locks all assets of a batch or of a person (taxonomy field
  `person` or a name in title, description or transcript), optionally setting consent.
- Audio/video sync suggestions from BWF timecode or capture time (`sync_suggestion`, migration
  0008); confirm or reject in the asset detail; confirmed pairs are written to the sidecars and
  restored by `rebuild-from-sidecars`.
- Optional content migration tool (`migration.enabled`, off by default): preview, typed
  confirmation, rename-only moves with journal and a per-OS relink CSV for DaVinci Resolve.

### Added (milestone M7)

- Disaster-recovery drill `scripts/dr-drill.sh` (restore from backup on a fresh server, then rebuild
  from sidecars; PASS/FAIL summary) and a nightly `dr-drill` CI job; "Disaster recovery" section in
  `docs/operations.md`.
- End-user guide in German and English (`docs/user-guide/`).

### Fixed (milestone M7)

- A replacement server no longer scans, files or backs up before the first user exists. Before,
  it picked up the existing library immediately, so the restore from the share was refused
  ("target database is not empty") and an empty backup could shadow the real ones.
- `RUSHBASE_SERVER_LEASE_SECONDS` below twice the heartbeat interval is rejected (the lease would
  expire between renewals and a second server could take over).

### Security (milestone M7)

- Derived files are served only for media/JSON extensions with `Content-Security-Policy: sandbox`;
  every response carries CSP, `X-Frame-Options`, `Referrer-Policy` and `nosniff`; request bodies
  are capped (`RUSHBASE_MAX_BODY_BYTES`).
- `RUSHBASE_TRUSTED_PROXIES` (uvicorn forwarded headers), cookie `Secure` derived from
  `RUSHBASE_PUBLIC_URL`, one-time `RUSHBASE_SETUP_TOKEN` (installer prints the setup URL, wizard
  asks for the code, `setup_token_required` in `GET /api/v1/setup`), `/metrics` needs
  `RUSHBASE_METRICS_TOKEN`.
- File names with backslashes/control characters are skipped by the scanner and rejected by
  `resolve_in_root`; no-overwrite renames are atomic (`renameat2`/`link`); temp files are created
  exclusively without following links; sidecar discovery ignores symlinks.
- ffmpeg/ffprobe: protocol whitelist `file,pipe`, `file:` inputs, playlists/scripts refused;
  ExifTool `--`; Pillow pixel cap 250 MP; model downloads re-verified when already present and
  capped to the pinned size.
- OAuth: per-IP limits on authorize/token/revoke, hourly registration cap, metadata-client
  negative cache and row cap, unused clients purged after one hour, consent page marks unverified
  clients; token issuance/refresh/revocation and rejected MCP tokens are audited; MCP rate
  limits also per account; `rules_activate` needs an admin account; personal API token scopes
  (`read`-only tokens cannot change anything).
- Result lists of workers are bounded (shots/files 10 000, transcript segments 100 000, ...).
- Installer: uv is installed from a checksum-pinned release archive; systemd units are sandboxed
  (`ProtectSystem=strict`, no capabilities, syscall filter for the server, `UMask=0077`); SMB
  mounts use `0660`/`0770`.
- Desktop: no worker command or library root from the webview (native folder dialog, config file
  or `RUSHBASE_CLI` only), per-sub-command flag allow-list, `http(s)` server URL validation, release
  builds fail while the updater key is the placeholder.
- Docs: threat model in `SECURITY.md`, hardening settings in `docs/operations.md`.

### Added (milestone M7, hardening part 1)

- Scale test (`scripts/perf/seed.py`, `bench.py`, report `docs/eval/2026-10-perf.md`): 100 000
  assets / 50 TB / 2.3 M vectors; search p95 for a single user 1.4 s -> 0.29 s (scenes 2.3 s ->
  0.50 s), facets 1.5 s -> 3 ms (cached), needs matching 27 s -> 17 ms per asset.
- Migration 0007 (additive): `enrollment_token.revoked_at` (revoking no longer changes the expiry),
  keyset sort indexes `ix_asset_sort_*`, `ix_audit_log_target`, HNSW indexes on `halfvec` (half the
  size) replacing the full-precision ones.
- Query encoders warm up in the background; `/search/capabilities` shows `warming`, `/health`
  reports `encoders`; query vectors are cached; encoding no longer blocks the event loop.
- `rebuild-from-sidecars` restores the AI section (tags with source and confidence, descriptions,
  transcript, audio events, review state, human-edited fields). Sidecars carry the transcript and
  audio events from now on.
- The Rushbase XMP of a filed photo is rewritten (temp + rename) when AI results or edits change
  title, description, rating or keywords; a camera's XMP is never touched.
- `install.sh --with-models` and `rushbase models pull --only MODEL:ROLE,...` (server text towers
  only); `doctor.sh` checks the models. PostgreSQL settings tuned in both compose files.
- Settings `RUSHBASE_QUERY_CACHE_SECONDS` (15) and `RUSHBASE_STATEMENT_TIMEOUT_SECONDS` (30).
- The server pauses scanning and filing while the library mount is gone and resumes by itself; the
  worker client retries for about two minutes (survives a server restart).

### Changed (milestone M7)

- Facets and statistics run as single grouped passes; ranked full-text search of shots and
  transcripts ranks a pool of 5000 matches; the tag filter is a semi-join; needs auto-matching
  shares the searches of an item between assets of a filing burst.

### Added (milestone M5, MCP server and needs lists)

- OAuth 2.1 authorization server in the FastAPI app (ADR-0010): discovery documents,
  consent page with Rushbase login, `/oauth/token` (PKCE S256 only, rotating refresh tokens with
  reuse detection), dynamic client registration, client ID metadata documents with an SSRF-safe
  fetcher, redirect allowlist, RFC 8707 audience bound to `RUSHBASE_PUBLIC_URL`, opaque tokens
  stored hashed, per-client rate limits. Migration 0006 (additive).
- MCP endpoint `/mcp` (Streamable HTTP, `mcp` SDK 2.3.0 pinned): 26 tools with shared schemas in
  `rushbase_core.mcp_tools` (search, assets, keyframes, similar, facets, statistics, coverage,
  gap analysis, needs lists, annotation with source `claude`, taxonomy, filing rules, processing),
  three resources and an audit log. No tool deletes or moves files.
- Needs lists (`/api/v1/needs`, web page `/needs`) with passive auto-matching of filed and
  re-classified assets; administration page "MCP / Claude" (connector URL, clients with revoke,
  extra redirect URIs, call log). `/health` reports `warnings` (`mcp_public_url_missing`).
- Docs: `docs/mcp.md` (connect to claude.ai, scopes, troubleshooting, generated tool reference).

### Added (milestone M6, companion)

- Desktop app (Tauri 2): Processor with backlog, hardware, one-click processing, live progress,
  graceful stop, stop time, idle-only and GPU temperature guard; first-run wizard; Library with local
  playback, selection, exports and Send to Resolve; tray, autostart and updater (both off by default).
- CLI: `login`, `search`, `browse`, `filters`, `asset get`, `select`, `paths resolve`, `export`,
  `resolve status|send|selftest`, `overview`, `mcp serve`, all with `--json` (`docs/cli-json.md`).
- Local MCP server (stdio and bearer-protected HTTP) mirroring the CLI; no tool deletes or moves files.
- DaVinci Resolve Studio integration (idempotent import, metadata, markers, clip colour) and exports
  to FCPXML 1.10, CMX 3600 EDL and metadata CSV.
- Installers workflow (Windows, macOS universal, Linux), AUR package, headless worker Docker images.
- `packages/ui`: `ProgressBar`.

### Added (milestone M4, server and web)

- Migration 0005: taxonomy, tags with provenance, transcripts, audio events, embeddings
  (`vector(768)`, HNSW), AI description columns, review state, golden labels, evaluation runs and
  collections; the full-text vector now covers AI summaries, transcript and approved tag labels.
- Ingestion of the `audio`, `embed`, `vision` and `classify` results with human > claude > ai
  precedence, review reasons and thresholds, and sidecar refresh for filed assets.
- Taxonomy API (proposals, approve, merge, rename, synonyms, deprecate), `GET /api/v1/worker/taxonomy`,
  dynamic facets and tag/AI-attribute filters.
- Hybrid search (`/api/v1/search`: full-text, e5 and SigLIP fused by RRF, assets or scenes),
  `/search/capabilities`, find similar for assets and shots, admin re-embed; query encoders on ONNX
  Runtime (CPU). See `docs/search.md`.
- Review queue, human edits of assets and shots, bulk edit and collections; golden set labelling,
  `rushbase-server eval` and `docs/eval/`.
- Web: hybrid search with Assets/Scenes toggle, asset tabs for description, tags, transcript and
  similar material, shot timeline with keyframe thumbnails and audio events, `/review`, `/golden`,
  `/admin/taxonomy`, search administration; technical metadata with units and translated labels.

### Added (milestone M4, worker AI stages)

- Hardware detection (NVIDIA, AMD, Intel, Apple, Vulkan, CPU features) with backend and profile
  choice (`xl`/`gpu8`/`gpu4`/`cpu`); `rushbase hardware`.
- Pinned, resumable, SHA-256 verified downloads of llama.cpp builds and models
  (`binaries.lock.json`, `models.lock.json`, `worker/scripts/update-lock.py`); `rushbase models`.
- Stages `audio` (Whisper, CED events, EBU R128, BPM), `embed` (SigLIP 2), `vision` (Qwen3.5 via
  llama-server, schema-constrained, versioned prompts), `classify` (e5, taxonomy mapping,
  proposals, review reasons); GPU lifecycle keeps VLM and Whisper apart; `rushbase bench`.
- `whisper-build.yml` workflow and Docker build scripts for whisper.cpp.

### Added (milestone M2, CPU worker)

- `rushbase-worker`: headless engine (config, retrying API client, claim loop with heartbeats,
  graceful release on SIGINT/SIGTERM or stop time, mount check against the server's library id)
  and stages `probe` (ffprobe + ExifTool + sidecar XML via a data-driven mapping table, decode
  status per ADR-0003), `hash` (streaming BLAKE3), `preview` (poster, hover sprite, 720p proxy,
  waveform + peaks, photo previews incl. HEIC/RAW, dHash, optional LUT normalisation) and `shots`
  (PySceneDetect, keyframes, camera motion, quality).
- CLI: `enroll`, `process` (alias `worker run`), `status`, `analyze`, extended `doctor`; JSON
  contracts in `docs/cli-json.md`; documentation in `docs/worker.md`.
- `worker/scripts/fetch-exiftool.py` installs the pinned, checksum-verified ExifTool into the
  toolchain.

### Added (milestone M2, server filing)

- Result ingestion: probe, hash, preview and shots outputs are validated against
  `rushbase_core.results` (invalid output fails the job permanently with a clear message) and
  normalised into asset columns, `asset_metadata`, `asset_file.blake3`, derived file references and
  `shot` rows. Migration `0003` (additive): `decode_status`, `derived`, `perceptual_hash`,
  technical summaries, `quarantine` record and facet indexes.
- Duplicates: an asset whose files all match one filed asset by BLAKE3 becomes `duplicate`, stays
  in `Input/` and is counted as wasted bytes.
- Filing rules (`rushbase_core.filing`): template language with fallbacks and date formats,
  sanitiser, path budget, case-insensitive folder reconciliation; versioned rules with dry-run and
  activation (`/api/v1/filing-rules`). See `docs/filing.md`.
- `finalize` now files assets: rename into `Content/` (card structures as one directory), never
  overwrite (`_2`, `_3`), sidecars (`_meta/<file>.json`), photo XMP, optional central copy, move
  journal (DB and `.rushbase/journal/*.jsonl`).
- Quarantine of permanently failed assets with `error.json`, list and retry API.
- `rushbase-server rebuild-from-sidecars [--dry-run]`.
- Read APIs: `/api/v1/assets`, `/assets/{id}`, `/facets`, `/stats`, `/batches`,
  `/derived/{asset_id}/{path}` (Range, cache headers) and library settings (per-OS path roots).

### Added (milestone M3, integration)

- Web UI wired to the real server: types come from the generated API client, MSW mocks are typed
  from the same schema; sort values, sprite tiles, filing rule versions, library settings and
  quarantine follow the server. Facet counts follow the active filters and query.
- `GET /api/v1/admin/enrollment-tokens`, `POST /api/v1/admin/enrollment-tokens/{id}/revoke` and
  `POST /api/v1/setup/restore` (restore a backup from the setup wizard).
- `make e2e-real`: Playwright run against a real server, fresh database and the real worker.
- `make dev` always serves this checkout's `dev/library`.

### Added (milestone M3, search)

- Full-text search over title, file name, batch, summaries, camera data and paths: accent-insensitive
  configuration `rushbase_simple` (`Straße` = `strasse`), weighted `asset.search_tsv` and
  `shot.search_tsv` kept current by triggers (migration `0004`, additive, with backfill), prefix
  matching on the last word, relevance ordering with an exact keyset cursor, and a substring
  fallback for queries under three characters. `/api/v1/facets` accepts the same filters and `q`
  as `/api/v1/assets`.

### Added (milestone M3, web UI)

- Web app: setup wizard, login, dashboard with a live "material flows" pipeline, library search with
  URL-synced facets, tile grid with hover scrubbing, list view and multi-select, asset detail with
  player, shot timeline, grouped technical data and per-OS file paths, processing (queue, batches,
  workers, quarantine retry) and admin (users, worker tokens, filing rules with dry run, path
  mapping, backups). German and English, light and dark, keyboard first (command palette, `/`, `?`).
- `packages/ui`: accessible component library on Radix and cmdk with OKLCH design tokens.
- Mock API (MSW) for demos, tests and Playwright; Playwright and axe suite with CI browser cache;
  screenshots in `docs/screenshots/`. ADR-0013.

### Added (milestone M1, server core)

- `rushbase-core`: UUIDv7 ids, media type tables, pure asset grouping (RAW+JPEG, sidecars, camera
  card structures, `*.RDC`, image sequences), quick fingerprint (BLAKE3 of three 64 KiB samples),
  scan stability rules, stage DAG planning, batch naming, version comparison and the
  `library.json` server lease; manifest writes use temp + fsync + rename (ADR-0005).
- `rushbase-server`: M1 database schema (migration `0002`, additive and idempotent, documented in
  `docs/architecture/data-model.md`), input scanner, job queue with `SKIP LOCKED` leasing,
  stage-batched draining, heartbeat, graceful release, idempotent results, lease reaper with
  exponential backoff, quarantine intent after the maximum number of attempts, per-batch priority
  and pause, global pause, re-processing by model version.
- Worker API (`/api/v1/worker/*`): enrollment with one-time tokens, API keys stored hashed, claim,
  heartbeat, result, release and library check.
- Authentication: argon2id users with roles (admin, editor, viewer), cookie sessions with CSRF
  double-submit, login rate limit, personal API tokens, first-run setup, audit log.
- Backups: scheduled `pg_dump` (custom format, every 6 h plus daily) with retention and a JSON
  export of settings and filing rules into `.rushbase/backups/`; `rushbase-server backup
now|list|restore`, `rushbase-server migrate`; the setup status offers a restore when backups exist.
- Server lease with `--force-takeover`; `/health` reports database, library mount and lease;
  `/api/v1/status` reports the backlog per stage (jobs, files, bytes, media duration) without any
  worker online; optional Prometheus `/metrics`.
- Optional in-process `hash` worker (`RUSHBASE_INPROCESS_STAGES`) for setups without workers.
- `scripts/kill-test.sh`: end-to-end test that SIGKILLs a worker mid-job and asserts re-leasing.

### Changed

- `rushbase-server` is now a Typer CLI (`rushbase-server` still starts the server).
- `/health` response gained `library_mounted`, `lease_held` and `setup_required`.

### Added (milestone M0, foundation)

- Operations layer (milestone M7): one-shot `install.sh` (bare metal with PostgreSQL 16 +
  pgvector, or Docker Compose in bridge/host network mode, optional SMB mount), `update.sh`
  (backup, migrate, health check, automatic rollback, update trains `main`/`dev`, optional SSH
  signature verification), `revert-update.sh`, `doctor.sh [--repair] [--json]`, systemd units for
  the server, watchdog, updater (notify-only by default) and a Unix-socket trigger for the
  backend, reverse proxy/tunnel examples (Caddy, Nginx, cloudflared), Proxmox LXC and TrueNAS
  guides, `docs/operations.md`, and `scripts/sandbox-test.sh` (end-to-end test in a systemd
  container) with ShellCheck and sandbox jobs in CI.

- Monorepo foundation (milestone M0): uv workspace (`core`, `server`, `worker`) and pnpm
  workspace (`web`, `packages/ui`, `packages/api-client`).
- `rushbase-core`: `library.json` models with server lease, library layout constants,
  library-relative path utilities with per-OS root mapping, ignore-pattern matcher,
  `init_library` / `open_library`.
- `rushbase-server`: FastAPI app factory, `RUSHBASE_*` settings (no default secrets), `/health`
  with database reachability, SQLAlchemy 2 async engine (psycopg 3), Alembic baseline enabling
  `vector` and `pg_trgm`, JSON logging, `rushbase-server` entry point.
- `rushbase-worker`: `rushbase` CLI with `version` and `doctor`; stable `--json` output
  documented in `docs/cli-json.md`.
- Web app with landing/health page, dark mode and German/English toggle; shared UI library
  (Button, Card, Badge, ProgressRing, ThemeProvider) and generated API client.
- Development stack (`rushbase-dev` compose project: PostgreSQL + pgvector, Samba test NAS),
  `Makefile`, isolated toolchain scripts and a deterministic synthetic test media generator.
- GitHub Actions CI and Dependabot configuration.
- Project documentation: `CLAUDE.md`, ADR scaffold and ADR 0001, `docs/STATUS.md`,
  `docs/UPDATE_RULES.md`, `CONTRIBUTING.md`, `SECURITY.md`, `THIRD_PARTY.md`.
