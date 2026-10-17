# Project status

End-user guide: [Deutsch](user-guide/de.md) | [English](user-guide/en.md). Disaster recovery: [operations.md](operations.md#disaster-recovery) (drill: `scripts/dr-drill.sh`).

| Milestone      | Scope                                                                                                                                                  | Status                                                                                                        |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| M0 Foundation  | Monorepo, licence, ADR scaffold, CI, dev environment (PostgreSQL + Samba), test media                                                                  | **done**                                                                                                      |
| M1 Server core | Schema and migrations, library init, input scanner, queue with leasing, worker API, auth, backups, server lease                                        | **done**                                                                                                      |
| M2 CPU worker  | `probe`, `hash`, `preview`, `shots`; sidecars + XMP; filing rules; move, quarantine, journal                                                           | **done** — worker engine + server filing merged; verified end to end on test media                            |
| M3 Web UI v1   | Search, detail view, processing, statistics, admin, Playwright E2E                                                                                     | **done** — real UI against the real server, full-text search, real-stack E2E (`make e2e-real`)                |
| M4 AI stages   | whisper.cpp, embeddings, VLM with schema output, taxonomy, hybrid search, review list, eval harness                                                    | **done** — all AI stages verified end to end on the reference RTX 3070 (`docs/eval/2026-10-vlm-benchmark.md`) |
| M5 MCP         | OAuth, all tools, wish lists, "connect in claude.ai" guide                                                                                             | **done** (server and web; the real claude.ai connector test needs a public HTTPS URL, see `docs/mcp.md`)      |
| M6 Companion   | Tauri GUI, CLI, local MCP, hardware detection, Resolve integration, installers                                                                         | **done** — installers build in CI for Linux, Windows and macOS (Apple Silicon); manual test on Windows/macOS and real Resolve pending (`docs/companion.md`)                      |
| Operations     | Installer (bare/Docker), updater with rollback and trains, watchdog, doctor, systemd units, proxy/tunnel examples, sandbox test (`docs/operations.md`) | **done** — bare install, update, rollback, watchdog and doctor verified in the systemd sandbox; worker images build in CI                                              |
| M7 Hardening   | Installer/updater, doctor, watchdog, performance at 100k assets, security review, v1.0                                                                 | **done except owner items** — performance, security review + fixes, DR drill, docs; open: release signing, real claude.ai connector, manual test matrix (`docs/release-checklist.md`)                               |

## Spec gaps (branch `feature/spec-gaps`)

- Delete to trash and restore, retention setting and expiry purge (`services/trash.py`,
  `api/trash.py`, admin tab _Trash_); admin and browser session only.
- Privacy: consent/person flag in asset detail and bulk edit, lock by batch or person
  (`services/privacy.py`, page _Privacy_). Face recognition stays off and is not implemented.
- Audio/video sync suggestions (`services/sync.py`, asset tab _Audio sync_); FCPXML export of
  confirmed synced audio is not done.
- Content migration tool (`services/migration.py`, admin tab _Migration_), off by default, never
  reachable through MCP.
- Migration 0008: `trash_item`, `sync_suggestion`.

## Final gaps (branch `feature/final-gaps`, migration 0009)

- "Used in project" (spec 4/8, ADR-0002): `asset_usage`, `rushbase resolve sync-usage`
  (fake-Resolve tests only, never run against a real Resolve), `POST /api/v1/usage/report` and
  `/api/v1/worker/usage`, facet and filter `used_in_project`, asset tab _Usage_.
- Reprocess by model (spec 5): `GET /api/v1/admin/reprocess/models`, `POST
  /api/v1/admin/reprocess/by-model` (dry run by default), MCP `reprocess` with `model`/`dry_run`,
  dialog in _Processing_.
- Dashboard (spec 12): `/api/v1/stats/growth|throughput|dimensions|coverage`, index
  `ix_stage_result_finished_at`.
- Audit-log viewer (spec 12): `GET /api/v1/admin/audit`, admin tab _Audit log_.
- Linked needs lists in the asset detail (spec 12): `GET /api/v1/needs/for-asset/{id}`.
- Personal API token expiry: `api_token.expires_at`, tab _API tokens_.

Still open: FCPXML export of confirmed synced audio; face clustering feature flag (out of scope,
off by default); real-Resolve verification of `GetClipProperty("Usage")` (run
`rushbase resolve sync-usage --dry-run` once on a Studio machine); `asset_usage` is feedback only
and is not part of the sidecars, so `rebuild-from-sidecars` starts it empty until the next sync.

## M6 companion

Branch `feature/m6-companion`. Full guide: `docs/companion.md`; CLI contract: `docs/cli-json.md`.

- **Worker library/CLI** (`worker/src/rushbase_worker/companion/`, `cli_companion.py`): `login`, `search`,
  `browse`, `filters`, `asset get`, `select ...`, `paths resolve`, `export fcpxml|edl|csv`,
  `resolve status|send|selftest`, `overview`, `mcp serve`; `process` records per-stage throughput for
  the duration estimate.
- **Local MCP server** (official `mcp` SDK 2.3.0, `MCPServer`): 18 tools, stdio and bearer-protected
  streamable HTTP; tested with the SDK client in memory and over real stdio.
- **Resolve** (ADR-0002): bridge script run by `ResolvePython`, tested against a stateful fake
  `DaVinciResolveScript`. **Not run against a real Resolve**; `resolve selftest` exists for the owner.
- **Desktop app** (`desktop/`, Tauri 2.12.4): Processor (backlog, hardware, one-click run, live
  progress, graceful release, stop time, idle-only, GPU temperature guard), first-run wizard, Library
  (search, filters, local playback, selection, exports, Send to Resolve), tray, autostart (off), updater
  (off until keys exist). Worker as a PyInstaller sidecar (`desktop/scripts/build-sidecar.sh`).
- **Packaging**: `.github/workflows/desktop.yml` (Windows, macOS universal, Linux), `deploy/aur`,
  `deploy/docker/worker.Dockerfile` (cpu, cuda, vulkan, rocm).
- **Screenshots**: `docs/screenshots/desktop-*.png` (mock host, Playwright).

Open: Windows and macOS builds (CI only), graceful stop on Windows (`taskkill` without `/F`, the
lease expires otherwise), real-Resolve verification of marker frame offsets and metadata key names,
FCPXML against Apple's DTD, Wayland drag-out (hidden by design), a model benchmark in the wizard.

## M4 worker (AI stages)

Implemented on `feature/m4-worker-ai`: hardware detection, pinned model/binary manager, llama-server
and whisper.cpp runtimes, stages `audio`, `embed`, `vision`, `classify`, `rushbase bench`.
Details, profiles and measured numbers: `docs/worker-ai.md`, `docs/eval/2026-10-vlm-benchmark.md`.

```sh
uv run rushbase hardware --json
uv run rushbase models pull --profile gpu8        # ~12 GB, resumable, SHA-256 verified
uv run rushbase analyze clip.mp4 --stages probe,preview,shots,audio,embed,vision,classify
```

Open: server endpoint `GET /api/v1/worker/taxonomy` (the client falls back to an empty snapshot),
Parakeet and Granite-Speech pilots (ADR-0011), Harrier text-embedding benchmark, musical key detection.

## M2 worker (engine part)

The CPU worker engine is implemented on `feature/m2-worker-engine` (server-side filing and
sidecars are separate work). Demo against a running server (see M1 demo):

```sh
python3 worker/scripts/fetch-exiftool.py          # pinned ExifTool into the toolchain
uv run rushbase enroll --server http://127.0.0.1:18080 --token <token> --name dev-worker
uv run rushbase doctor --library dev/library
uv run rushbase process --exit-when-empty         # probe, hash, preview, shots
uv run rushbase analyze dev/library/Input/<file>.mp4 --json   # no server needed
```

Details: `docs/worker.md`. The integration test `worker/tests/test_integration.py` starts a real
server on port 18081 (needs `RUSHBASE_TEST_DATABASE_URL`).

## M0 demo

Prerequisites: Docker, and nothing else on the host: `make setup` fetches Python 3.12 (uv),
pnpm packages and, if the Docker Compose plugin is missing, a checksum-verified standalone
`docker-compose` into the project toolchain.

```sh
make setup           # Python 3.12 via uv, Python and web dependencies
make lint typecheck test
make testmedia       # synthetic clips, audio, photos, camera-card structure, junk files
make dev             # postgres (15432), samba (14450), migrations, server (18080), web (15173)
```

Then, in another terminal:

```sh
curl -fsS http://127.0.0.1:18080/health          # {"status":"ok","version":"0.1.0","database":true}
curl -fsS http://127.0.0.1:15173/api/health      # same, through the Vite proxy
# browse to http://127.0.0.1:15173               # landing page: health ring, dark mode, DE/EN toggle
uv run rushbase doctor --library dev/library --server-url http://127.0.0.1:18080
make dev-down        # stops only the rushbase-dev compose project
```

`rushbase doctor` reports `error` for missing `ffmpeg`, `ffprobe` or `exiftool`; install them
on the machine that runs a worker. Exit code 1 means at least one check failed.

## M1 demo

Prerequisites as for M0 (`make setup`, Docker). Everything below runs against the isolated dev
stack.

```sh
make lint typecheck test       # includes the PostgreSQL tests (each session uses its own database)
make dev-up                    # postgres + samba, applies migrations 0001 and 0002
scripts/kill-test.sh           # SIGKILLs a fake worker mid-job, asserts the job is re-leased
```

Run the server and walk through the flow (dev credentials come from `dev/data/dev.env`):

```sh
scripts/with-dev-env.sh uv run rushbase-server &      # lease taken in dev/library/.rushbase/library.json
curl -fsS http://127.0.0.1:18080/health               # database, library_mounted, lease_held, setup_required
curl -fsS http://127.0.0.1:18080/api/v1/setup         # needs_setup, restore_available
curl -fsS -c jar -X POST http://127.0.0.1:18080/api/v1/setup \
  -H 'content-type: application/json' -d '{"username":"admin","password":"choose a long password"}'
make testmedia                                        # junk, sidecars, a camera card into dev/library/Input
# after the stability window (2 scans and 120 s by default) the files become assets with jobs:
curl -fsS -b jar http://127.0.0.1:18080/api/v1/status # backlog per stage: jobs, files, bytes, duration
CSRF=$(grep rb_csrf jar | awk '{print $7}')
curl -fsS -b jar -X POST http://127.0.0.1:18080/api/v1/admin/enrollment-tokens \
  -H "X-CSRF-Token: $CSRF" -H 'content-type: application/json' -d '{"note":"demo"}'
scripts/with-dev-env.sh uv run rushbase-server backup now
scripts/with-dev-env.sh uv run rushbase-server backup list
```

The backup commands need `pg_dump`; without a local PostgreSQL client set
`RUSHBASE_PG_DOCKER_CONTAINER=rushbase-dev-postgres-1` (development only).

The OpenAPI schema (`/openapi.json`, client in `packages/api-client`) documents the worker protocol
(`/api/v1/worker/*`), authentication, administration and `/api/v1/status`.

### Notes on M1 behaviour

- `finalize` runs on the server. Filing (rules, rename, sidecars, journal) arrives with M2, so in
  M1 it only records that the analysis is complete and moves the asset to `review`.
- Failed assets are marked `quarantined` and the intent is written to the audit log; the physical
  move to `.rushbase/quarantine/` is part of M2.
- Default pipeline: `probe, hash, preview, shots, finalize`. The AI stages (`audio`, `embed`,
  `vision`, `classify`) exist in the schema and the planner but are off until M4.
- Without any worker the server still serves status, backlog and administration (ADR-0012). Setting
  `RUSHBASE_INPROCESS_STAGES=hash` lets the server hash files itself; `probe` joins in M2.
- Restoring: start `rushbase-server` against an empty database on an existing library; `GET
/api/v1/setup` lists the backups, then `rushbase-server backup restore <file>`.
- `scripts/kill-test.sh` starts a private server with short timings when nothing listens on 18080;
  against a running dev server set `KILL_TEST_USER`/`KILL_TEST_PASSWORD` (it then needs minutes
  because of the default 120 s lease).

## M2 server (filing)

Everything below runs on the server; the stage outputs arrive through the worker API.

- Ingestion, duplicates, filing rules, `finalize` (rename, sidecars, XMP, journal), quarantine and
  `rebuild-from-sidecars` are described in `docs/filing.md` and `docs/architecture/data-model.md`.
- Try it without a worker: put files in `Input/`, post fake results to `/api/v1/worker/jobs/{id}/result`
  (see `server/tests/pipeline.py`), the maintenance loop (15 s) files the asset.
- `rushbase-server rebuild-from-sidecars --dry-run` reports what a rebuild would restore.
- Decisions: a camera card is filed once every member asset left the pipeline (analysed, duplicate
  or quarantined); if foreign files share the card directory its members move file by file below a
  new root with the structure kept. Quarantine moves only the asset's own files unless it is the sole
  member of its card directory. `review` means "analysed, waiting for the rest of its card".

## M3 web UI

The web app (`web/`, shared components in `packages/ui`) runs against the real server. Stack,
styling and the colour/motion language are in `docs/adr/0013-web-ui-stack.md`. API types come from
the generated client (`packages/api-client`, `make gen-api`); the MSW mocks are typed from the same
schema, so UI and server cannot drift apart.

### Demo with the real stack

```sh
make setup
make dev                  # postgres, samba, migrations, server :18080 (library: dev/library), Vite :15173
```

1. Open http://127.0.0.1:15173 and walk through the setup wizard (language, admin, library name,
   optional per-OS paths). The last step shows the worker command with a fresh one-time token.
2. Enroll the worker with that command. The worker keeps its key in a data directory; keep it inside
   the checkout while developing:
   `RUSHBASE_DATA_DIR=dev/data/worker uv run rushbase enroll --server http://127.0.0.1:18080 --token <token> --name dev-worker`
3. `make testmedia` puts synthetic clips, photos, audio, a camera card and junk into `dev/library/Input`.
   The scanner groups them after the stability window (2 scans, 120 s by default; set
   `RUSHBASE_STABLE_SECONDS=0 RUSHBASE_STABLE_SCANS=1 RUSHBASE_SCAN_INTERVAL_SECONDS=2` before
   `make dev` to see it in seconds).
4. `RUSHBASE_DATA_DIR=dev/data/worker RUSHBASE_LIBRARY_ROOT=$PWD/dev/library uv run rushbase process --exit-when-empty`
   runs probe, hash, preview and shots; the server then files the assets.
5. Browse: the dashboard counts filed clips, the library searches (`uhd`, `strasse`, prefixes) and
   filters with facet counts that follow the query, the asset page plays the proxy (HTTP Range) and
   lists shots and keyframes, Processing shows workers, batches and quarantine, Administration
   manages users, worker tokens, filing rule versions (dry run, activate), per-OS paths and backups.

Mock demo without a server: `VITE_MOCK=1 pnpm --filter @rushbase/web dev` (sign in as `elias`, `mara`
or `jonas` with `rushbase-demo-pass`; scenarios `fresh` and `no-workers` via
`sessionStorage["rushbase.mock.scenario"]`).

### Tests

```sh
pnpm -r test                                                    # Vitest + Testing Library + MSW
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.toolchain/playwright"
pnpm --filter @rushbase/web exec playwright install chromium    # once
pnpm --filter @rushbase/web e2e                                 # mock E2E (CI): axe checks, light and dark
make e2e-real                                                   # real server + database + worker
RUSHBASE_E2E_SCREENSHOTS=1 make e2e-real                        # also retakes docs/screenshots from the real stack
```

`make e2e-real` (`scripts/e2e-real.sh`) needs `make dev-up` and takes about 30 seconds. It creates
the database `rushbase_e2e_web`, a temporary library below `dev/data/e2e-real/` seeded by the test
media generator, starts the server on :18083 and Vite on :15175, then drives setup wizard, worker
enrollment with the command shown by the UI, `rushbase process --exit-when-empty`, dashboard,
search, proxy playback (206 for a Range request), processing and token creation. Everything is
removed afterwards (`KEEP_E2E_DATA=1` keeps the files). It is a manual or nightly check, not part of
the per-push CI job.

### Contract decisions

The server is the source of truth for shapes it already had; the UI adapted:
`sprite_tiles` is the number of frames of a one-row contact sheet; `sort` is
`-date|date|-created_at|created_at|-size|size|-duration|duration|filename` (a leading `-` means
descending, searches rank by relevance until a sort is picked); keyframe `position` is
`start|middle|end`; `/stats` backlog is the per-stage backlog of `/status`. Admin pages use
`/api/v1/filing-rules` (immutable versions with dry run and activation), `/admin/library-settings`
(per-OS roots), `/quarantine` and `/quarantine/{id}/retry`. Added on the server for the UI:
`GET /admin/enrollment-tokens` (no secrets), `POST /admin/enrollment-tokens/{id}/revoke` (expires an
unused token now), `POST /setup/restore` (only while the database is empty). The enroll command is
`rushbase enroll --server <url> --token <token> --name <name>`.

## M4 server and web (AI)

Server side of milestone M4 (the worker stages are described above).

- **Ingestion:** `services/ai_results.py` validates and stores the four AI outputs (replace per
  stage; human/claude rows, edited columns, locked assets and removed tags are protected). Review
  state comes from `review_reasons` and the per-field confidence thresholds (app setting
  `ai.thresholds`). Details: `docs/architecture/data-model.md`.
- **Taxonomy:** `docs/taxonomy.md`. **Search:** `docs/search.md`. **Evaluation:** `docs/eval/README.md`.
- **Try it:** point `RUSHBASE_MODELS_DIR` at a worker model store (dev:
  `.toolchain/worker-data/models`), run a worker with the AI stages, open the library, search for a
  sentence from a video and switch to "Szenen". `/review`, `/golden` and `/admin/taxonomy` need the
  editor role.
- **Decisions:** embeddings live in `embedding_image` and `embedding_text` (not one table per
  dimension as in ADR-0007) because the dimension is fixed at 768 and the model id/revision columns
  already separate spaces; SigLIP vectors are matched by model id only (the worker's revision
  depends on the vision precision), e5 by the lock-derived revision; search by example image
  answers 501 (ADR-0012); a classification proposal without a field lands in the field `unsorted`.
- **Open:** `rebuild-from-sidecars` does not yet restore tags, descriptions or transcripts from the
  sidecar `ai` section; XMP keywords are written at filing but not refreshed afterwards; the
  re-embed action requires a worker for the `embed` stage.

## M5 MCP server and needs lists

Guide: `docs/mcp.md`. Code: `server/src/rushbase_server/{oauth,mcp}/`, tool contract in
`core/src/rushbase_core/mcp_tools.py` (reusable by the companion's local MCP server).

- **OAuth 2.1** (ADR-0010) in the FastAPI process: metadata at `/.well-known/oauth-authorization-server`
  and `/.well-known/oauth-protected-resource[/mcp]`, `/oauth/authorize` (server-rendered consent with
  Rushbase login; editor/admin only), `/oauth/token`, `/oauth/register` (DCR), `/oauth/revoke`, CIMD
  client ids (https URL, SSRF-safe fetch: public addresses only, the connection goes to the resolved
  IP, no redirects, 5 s, 64 KiB, cached by `Cache-Control`/`ETag`). Tokens are opaque and stored
  hashed; access 1 h, refresh 30 d sliding with rotation and reuse detection (family revoked).
- **MCP** `/mcp`: `mcp==2.3.0`, stateless Streamable HTTP with JSON responses behind an own ASGI
  gateway (bearer verification, audience, `WWW-Authenticate` with `resource_metadata` and
  `scope="read"`, per-client token bucket). Tool inputs are built from the pydantic models of the
  shared module; each call is audited in `mcp_audit` (arguments only as a hash).
- **Needs lists:** tables `need_list`, `need_item`, `need_candidate`; REST `/api/v1/needs`, page
  `/needs`; `Runtime.match_needs` runs after finalize (filed) and after `classify` results.
- **Decisions:** Pillow downscales thumbnails (256 px) and keyframes (512 px) on the server from the
  derived JPEGs; notes of `annotate_asset` live in `asset.ai_details["notes"]` (no schema change);
  Claude's column edits (rating, lock, consent) are audited but not protected from later human edits,
  Claude's tags never override or remove human tags; reads use scope `read`, needs tools scope `needs`.
- **Inspector:** `npx @modelcontextprotocol/inspector --cli http://localhost:<port>/mcp --transport http --header "Authorization: Bearer <token>"` (npm cache in the toolchain) succeeded for `tools/list`, `tools/call library_stats` and `resources/list` against a locally running server with a token from the real OAuth flow (DCR, PKCE).
- **Not verified here:** the real claude.ai connector (needs a public HTTPS URL).

## M7 security

Branch `feature/m7-security`: findings M1, M2, M3 (uv pinning only), M4 and L1 to L14 of the
security review. Summary in `CHANGELOG.md` ("Security"), threat model in `SECURITY.md`, settings in
`docs/operations.md` ("Hardening settings"). Open: signed-update enforcement default
(`RUSHBASE_UPDATE_REQUIRE_SIGNED`, owner decision), personal API token expiry (needs a column).

## M7 hardening, part 1 (performance and robustness)

- **Scale test:** `scripts/perf/seed.py` builds the 100 000-asset catalog in a dedicated database,
  `bench.py` drives the real server (4 pinned cores) and writes the report. Result and open points:
  `docs/eval/2026-10-perf.md` (single-user search p95 under 500 ms; multi-filter semantic search and
  eight simultaneous searchers are bound by RAM/cores on the 8 GB / 4 core rig).
- **Migration 0007** (additive, reversible): `enrollment_token.revoked_at`, sort and audit indexes,
  halfvec HNSW indexes (rebuild takes minutes on large catalogs).
- **Backlog closed:** encoder warm-up, AI section in `rebuild-from-sidecars`, XMP refresh,
  revocable enrollment tokens, `/health` encoder state, `install.sh --with-models`, doctor check.
- **Robustness:** library mount loss pauses the server's file work and recovers (tested);
  worker client rides out a server restart (tested with a real server process).
- **Decisions:** caches (15 s, stale-while-revalidate) only for facets, statistics and vector counts;
  a 5000-match ranking pool for shot/transcript full-text; halfvec indexes instead of tuning
  `ef_search`; Postgres tuning shipped as compose flags (Docker) or per-database settings (bare).
- **Open:** security review fixes are merged separately; filtered semantic search on machines with
  less than 16 GB RAM; real Windows/macOS companion builds (M6).
