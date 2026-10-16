# Project status

| Milestone | Scope | Status |
| --- | --- | --- |
| M0 Foundation | Monorepo, licence, ADR scaffold, CI, dev environment (PostgreSQL + Samba), test media | **done** (pending review) |
| M1 Server core | Schema and migrations, library init, input scanner, queue with leasing, worker API, auth, backups, server lease | **done** (pending review) |
| M2 CPU worker | `probe`, `hash`, `preview`, `shots`; sidecars + XMP; filing rules; move, quarantine, journal | server side **done** (pending review); worker engine and UI in separate branches |
| M3 Web UI v1 | Search, detail view, processing, statistics, admin, Playwright E2E | planned |
| M4 AI stages | whisper.cpp, embeddings, VLM with schema output, taxonomy, hybrid search, review list, eval harness | planned |
| M5 MCP | OAuth, all tools, wish lists, "connect in claude.ai" guide | planned |
| M6 Companion | Tauri GUI, CLI, local MCP, hardware detection, Resolve integration, installers | planned |
| Operations | Installer (bare/Docker), updater with rollback and trains, watchdog, doctor, systemd units, proxy/tunnel examples, sandbox test (`docs/operations.md`) | **done** (pending review; Docker mode validated statically only) |
| M7 Hardening | Installer/updater, doctor, watchdog, performance at 100k assets, security review, v1.0 | planned |

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
