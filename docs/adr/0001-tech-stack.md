# 0001. Technology stack, monorepo and workspaces

- Status: accepted
- Date: 2026-10-13
- Deciders: project owner

## Context

Rushbase consists of a coordinator server, headless/GUI workers, a web UI and a desktop
companion. The architecture invariants require: originals untouched, a single writer for
`Content/`, workers that run on CPU, NVIDIA, AMD, Intel and Apple hardware without PyTorch, a
PostgreSQL database with vectors, and one shared component library for web and desktop.
Self-hosting on small servers (Docker, LXC) and unattended operation are first-class goals.

## Decision

**Backend (Python 3.12).** FastAPI, Pydantic v2, SQLAlchemy 2 (async) with psycopg 3, Alembic
migrations, PostgreSQL 16+ with the `vector` (pgvector) and `pg_trgm` extensions. The worker
engine and the `rushbase` CLI (Typer) share the `rushbase-core` package with the server.
Inference runs through llama.cpp (`llama-server`), whisper.cpp and ONNX Runtime subprocesses or
bindings; FFmpeg/ffprobe, ExifTool, LibRaw and libheif handle media. No PyTorch in the worker.

**Frontend.** React 18, TypeScript, Vite, TanStack Router and Query, i18next (German default,
English). Reusable components live in `packages/ui` (CSS custom properties for dark/light), the
API client in `packages/api-client` is generated from the server's OpenAPI schema with
`openapi-typescript`. The desktop app is Tauri 2 and reuses `packages/ui` (milestone M6).

**Monorepo.** One repository with two workspaces: a **uv workspace** (`core`, `server`, `worker`,
one lock file, Python pinned to 3.12) and a **pnpm workspace** (`web`, `packages/*`). Shared
tooling: ruff and mypy `--strict` and pytest for Python; ESLint (flat config), Prettier and
Vitest for TypeScript. Toolchains are project-local (`.toolchain/`) so development never
touches the host.

**Delivery.** Docker image (amd64 + arm64) with docker-compose, and bare-metal installs via
systemd. CI runs on GitHub Actions.

## Alternatives considered

| Option | Pros | Cons |
| --- | --- | --- |
| Poetry / pip-tools instead of uv | familiar | slower, no first-class workspaces |
| npm/yarn instead of pnpm | ubiquitous | weaker workspace isolation and disk use |
| Separate repositories per component | independent releases | cross-cutting changes (schemas, API) become multi-repo |
| PyTorch-based inference | large model zoo | heavy, vendor-specific GPU stacks, violates universal hardware goal |
| Electron instead of Tauri | one runtime | large bundles, weaker native integration for drag-out and sidecars |
| SQLite instead of PostgreSQL | simple | no pgvector, weaker concurrency for a coordinator with many workers |

## Consequences

- One lock file per ecosystem keeps dependency updates reviewable; Dependabot covers pip/uv,
  npm, GitHub Actions and Docker.
- Shared `rushbase-core` means the server and the CLI can never disagree about `library.json`
  or path handling, at the cost of coupling their release cadence.
- Python 3.12 is pinned; moving to a newer minor version requires updating `.python-version`,
  `requires-python`, CI and the Docker base image together.
- Inference backends and models are chosen per hardware at runtime and documented in later ADRs.
