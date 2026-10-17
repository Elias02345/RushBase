# Contributing to Rushbase

Thanks for helping! Please read `CLAUDE.md` first: it lists the architecture invariants and the
development isolation rules that apply to humans and AI agents alike.

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Branch model

- `main`: releases only, tagged `vX.Y.Z`. Never commit to it directly.
- `dev`: integration branch; all work lands here first.
- `feature/*`, `fix/*`, `chore/*`: short-lived, branch from `dev`, pull request into `dev`.

## Workflow

1. Branch from `dev`: `feature/<topic>` or `fix/<topic>`.
2. Keep changes small and focused; add or update tests and docs with the code.
3. Run `make lint typecheck test` locally. CI runs the same checks plus a PostgreSQL service.
4. Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat(server): ...`,
   `fix(worker): ...`, `docs: ...`). Public releases use signed commits and tags.
5. Open a pull request against `dev`. Describe the user-visible effect and mention any impact on
   in-place updates (see `docs/UPDATE_RULES.md`).

## Setup

```sh
make setup      # isolated toolchain: uv-managed Python 3.12, pnpm packages
make dev        # PostgreSQL + Samba (Docker), server on :18080, web on :15173
```

Requirements on the host: Docker, `make`, `curl`, Node.js (for pnpm) and `ffmpeg` for the test
media generator. Everything else is installed into the project-local `.toolchain/`.

The toolchain is isolated on purpose: use `make` targets or `scripts/with-env.sh <cmd>` instead of
installing tools system-wide, and do not edit shell rc files or anything under `/etc`. The dev
stack is the Docker Compose project `rushbase-dev` only (PostgreSQL :15432, Samba :14450, server
:18080, web :15173, all on 127.0.0.1); `make dev-down` stops just that project. Set
`RUSHBASE_TEST_DATABASE_URL` to include the PostgreSQL tests in `make test`.

## Standards

- Python: type-annotated, `mypy --strict`, ruff (lint + format), pytest. Public functions have
  docstrings.
- TypeScript: strict mode, ESLint flat config, Prettier, Vitest. No hard-coded UI strings: add
  keys to `web/src/locales/{de,en}.json`.
- Database: additive, idempotent Alembic migrations with a working `downgrade()`.
- Language: English for code, comments, commits and docs; the UI is German and English.
- Licences: new dependencies must be compatible with Apache-2.0 and are listed in
  `THIRD_PARTY.md`. Proprietary SDKs are never bundled.
- Architecture decisions go into `docs/adr/` (copy `0000-template.md`).

## Reporting issues

Bugs and ideas: open a GitHub issue using the templates (the bug template asks for the output of
`doctor.sh` / `rushbase doctor`). Security problems: see `SECURITY.md`, do not file them
publicly.
