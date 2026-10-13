# Project status

| Milestone | Scope | Status |
| --- | --- | --- |
| M0 Foundation | Monorepo, licence, ADR scaffold, CI, dev environment (PostgreSQL + Samba), test media | **done** (pending review) |
| M1 Server core | Schema and migrations, library init, input scanner, queue with leasing, worker API, auth, backups, server lease | planned |
| M2 CPU worker | `probe`, `hash`, `preview`, `shots`; sidecars + XMP; filing rules; move, quarantine, journal | planned |
| M3 Web UI v1 | Search, detail view, processing, statistics, admin, Playwright E2E | planned |
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
