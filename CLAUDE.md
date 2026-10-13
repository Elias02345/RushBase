# CLAUDE.md - Rushbase project rules for AI agents

Rushbase is a public, Apache-2.0, self-hosted AI media library (video, photo, audio on a
network share). Read `docs/STATUS.md` (milestones) and `docs/UPDATE_RULES.md` (sacred paths and
update contract) before changing anything. Repository: public, so no secrets, no brand or
customer names, no internal hostnames, anywhere.

## Architecture invariants (never change without an ADR and an explicit decision)

1. **Originals are never modified.** No metadata written into originals, no re-encoding, no
   deletion. Moving `Input/` to `Content/` is a rename on the same share, never a copy.
2. **Single writer for `Content/`.** Only the server moves files and writes sidecars. Workers
   write exclusively to `.rushbase/derived/<asset-id>/`.
3. **Stable paths.** Once a file is in `Content/` it is never moved automatically. Filing rules
   apply to new material only; organisation over time is virtual (index, collections, filters).
4. **Original files never flow through the server.** Server, workers and companion apps each
   mount the share themselves. Only metadata, jobs and derived previews travel over HTTP.
5. **Paths are stored relative to the library root** (`/`-separated); each machine maps the root.
6. **The database is rebuildable from sidecars** (`rushbase rebuild-from-sidecars`). PostgreSQL
   never lives on the SMB share.
7. **MCP never deletes or moves anything.** Deleting is admin-only, in the web UI, to
   `.rushbase/trash/` with a retention period.
8. **No own will.** Rushbase processes deterministically and stores; it makes no autonomous
   decisions, sends no messages, notifies nobody.
9. **No PyTorch in the worker.** Inference goes through llama.cpp, whisper.cpp and ONNX Runtime.
10. **Additive migrations only**, each with a working `downgrade()` and idempotent guards.

## Repository layout

```
core/     rushbase-core   library.json models, layout, path mapping, ignore rules
server/   rushbase-server FastAPI, SQLAlchemy 2 async + psycopg 3, Alembic
worker/   rushbase-worker engine + `rushbase` CLI (Typer); every command has --json
web/      React 18 + TS + Vite + TanStack Router/Query + i18next
packages/ui, packages/api-client   shared components / generated OpenAPI client
desktop/  Tauri 2 app (milestone M6, not scaffolded yet)
dev/      docker-compose.dev.yml, synthetic test media generator
scripts/  dev-env.sh, with-env.sh, helper scripts      docs/  ADRs, status, rules
```

## Language rules

- Code, comments, commit messages, developer docs, CLI and log output: **English**.
- User interface: **German (default) and English** via i18n; never hard-code UI strings.
- Conventional Commits (`feat(scope): ...`). Branches: `main` (releases), `dev` (integration),
  `feature/*` and `fix/*` from `dev`.

## Development isolation (important)

All tooling runs from the project-local toolchain; nothing is installed system-wide.

- Use `make` targets or `scripts/with-env.sh <cmd>` (sources `scripts/dev-env.sh`, which points
  uv, pnpm and cargo into `.toolchain/`). Never `pip install --user`, never `sudo`, never edit
  shell rc files, systemd units or anything under `/etc`, `~/.config`, `~/.local`.
- Docker: only the compose project **`rushbase-dev`**. Never stop, remove or modify other
  containers, volumes or networks.
- Dev ports (bound to 127.0.0.1): server 18080, PostgreSQL 15432, Samba 14450, web 15173.
- Generated data stays inside the working tree (`dev/library/`, `dev/data/`, both gitignored).
  `dev/data/dev.env` holds generated dev credentials and must never be committed.

## Sacred paths (the updater and any tool must never touch them)

`.env`, secret/encryption keys, the PostgreSQL data directory, the library's `Content/`,
`Input/` and `.rushbase/` (except regenerating `derived/`). Full list: `docs/UPDATE_RULES.md`.

## How to run things

```sh
make setup       # uv sync + pnpm install (isolated toolchain)
make lint        # ruff, eslint, prettier
make typecheck   # mypy --strict, tsc
make test        # pytest + vitest (set RUSHBASE_TEST_DATABASE_URL for PostgreSQL tests)
make dev         # postgres + samba + migrations + server :18080 + web :15173
make dev-down    # stop only the rushbase-dev stack
make testmedia   # synthetic media into dev/library/Input
make gen-api     # regenerate packages/api-client after server route changes
```

Run the smallest meaningful check first (single test file), then the full suite before a commit.

## Quality bar

Small typed modules, documented public functions, no dead code, no placeholders that pretend
to work. Security-relevant settings have no defaults (the server refuses to start without
`RUSHBASE_SECRET_KEY`). Every user-visible change goes into `CHANGELOG.md`; every architecture
decision into `docs/adr/`.
