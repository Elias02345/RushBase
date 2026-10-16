# Project status

| Milestone | Scope | Status |
| --- | --- | --- |
| M0 Foundation | Monorepo, licence, ADR scaffold, CI, dev environment (PostgreSQL + Samba), test media | **done** (pending review) |
| M1 Server core | Schema and migrations, library init, input scanner, queue with leasing, worker API, auth, backups, server lease | **done** (pending review) |
| M2 CPU worker | `probe`, `hash`, `preview`, `shots`; sidecars + XMP; filing rules; move, quarantine, journal | planned |
| M3 Web UI v1 | Search, detail view, processing, statistics, admin, Playwright E2E | web shell done (runs on mocks until the read APIs land) |
| M4 AI stages | whisper.cpp, embeddings, VLM with schema output, taxonomy, hybrid search, review list, eval harness | planned |
| M5 MCP | OAuth, all tools, wish lists, "connect in claude.ai" guide | planned |
| M6 Companion | Tauri GUI, CLI, local MCP, hardware detection, Resolve integration, installers | planned |
| Operations | Installer (bare/Docker), updater with rollback and trains, watchdog, doctor, systemd units, proxy/tunnel examples, sandbox test (`docs/operations.md`) | **done** (pending review; Docker mode validated statically only) |
| M7 Hardening | Installer/updater, doctor, watchdog, performance at 100k assets, security review, v1.0 | planned |

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

## M3 web UI

The web app (`web/`, shared components in `packages/ui`) is complete against mocked read endpoints.
Stack, styling and the colour/motion language are in `docs/adr/0013-web-ui-stack.md`.

```sh
make setup
VITE_MOCK=1 pnpm --filter @rushbase/web dev        # demo on http://127.0.0.1:15173, no server needed
# sign in as elias (admin), mara (editor) or jonas (viewer) with the password rushbase-demo-pass
pnpm --filter @rushbase/web build:mock && pnpm --filter @rushbase/web preview:mock
```

Against a real server run `make dev` (the Vite proxy forwards `/api/v1/*` and `/api/health`).
Mock scenarios (set `sessionStorage["rushbase.mock.scenario"]`): `fresh` shows the setup wizard,
`no-workers` shows the empty worker state.

Tests: `pnpm -r test` (Vitest + Testing Library + MSW) and the browser suite

```sh
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.toolchain/playwright"
pnpm --filter @rushbase/web exec playwright install chromium   # once
pnpm --filter @rushbase/web e2e                                 # login to processing, axe checks, light and dark
pnpm --filter @rushbase/web screenshots                         # refreshes docs/screenshots/
```

### Contracts the UI expects (`web/src/api/contracts.ts`)

Specified for the server agent and mocked until they are in OpenAPI: `GET /api/v1/assets`,
`GET /api/v1/assets/{id}`, `GET /api/v1/facets`, `GET /api/v1/stats`. Assumptions on details the
specification left open: `sprite_tiles` is `{cols, rows, count}` (frames row by row);
`metadata.normalized` is grouped by top-level key (`camera`, `lens`, `exposure`, `video`, `audio`,
`location`; unknown groups and flat scalars are shown too); date filters are `YYYY-MM-DD`; `sort` is
one of `capture_desc|capture_asc|name_asc|size_desc|duration_desc`; `cursor` is opaque.

Proposed endpoints without a server implementation yet (the UI degrades to an error state):
`POST /api/v1/assets/{id}/retry`, `GET /api/v1/admin/enrollment-tokens`,
`POST /api/v1/admin/enrollment-tokens/{id}/revoke`, `GET|PUT /api/v1/admin/settings/path-mapping`,
`GET|PUT /api/v1/admin/filing-rules`, `POST /api/v1/admin/filing-rules/preview`,
`POST /api/v1/setup/restore`. The worker command shown in the wizard is
`rushbase enroll --server <origin> --token <token>`.
