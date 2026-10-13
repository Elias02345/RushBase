# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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
