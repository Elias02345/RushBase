# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
