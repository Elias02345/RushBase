# Update rules

The updater (milestone M7) and every manual procedure must follow this contract. A change that
could break an in-place update of a live installation must be flagged in the pull request.

## Sacred paths - never touched by an update

| Path | Why |
| --- | --- |
| `.env` | instance secrets and local configuration |
| Secret / encryption keys (outside the app directory, e.g. `/data/secrets/`) | losing a key means losing the data it protects |
| PostgreSQL data directory / Docker volume | the live database |
| Library `Content/` | stable, user-owned originals and sidecars |
| Library `Input/` | material waiting to be ingested |
| Library `.rushbase/` (`meta`, `backups`, `quarantine`, `trash`, `journal`, `library.json`) | state needed to rebuild and restore; `derived/` may be regenerated, never deleted by the updater |
| TLS certificates and user-authored configuration overrides | operator-owned |

Application code, container images and dependencies are replaced on update; everything above is
preserved byte-for-byte.

## Update flow (always in this order)

1. **Check** - `git fetch origin`; compare local `HEAD` with the remote and report the delta.
2. **Backup** - database dump plus a copy of `.env` into a timestamped directory. Abort when the
   backup fails (a timed-out cloud upload is a warning, not a blocker).
3. **Pull** - fast-forward guard, then `git reset --hard origin/<branch>`. The repository is the
   source of truth for application code.
4. **Dependencies** - `uv sync --frozen` / `pnpm install --frozen-lockfile`, only if a lock file
   changed.
5. **Migrate** - `alembic upgrade head`.
6. **Restart** - `systemctl daemon-reload`, restart the service.
7. **Health check** - `GET /health` on the local port; failure triggers rollback.
8. **State** - write `status.json` (outcome, commit, timestamp) for the web UI.

**Rollback** (automatic on a failed health check, manual via `revert-update.sh`): stop the
service, reset to the pre-update commit, restore `.env` and the database dump, restart, record
the outcome and surface it in the web UI.

Public repositories verify signed commits/tags before applying an update.

## Migration rules

- Additive only: new tables, new nullable columns, new indexes.
- No `DROP TABLE`/`DROP COLUMN`, `TRUNCATE` or unconditional `DELETE` without a deprecation cycle.
- Every migration has a working `downgrade()` and is idempotent (existence guards, `IF NOT EXISTS`).
- New `NOT NULL` columns: add as nullable, backfill, then alter - in the same migration.
- The baseline migration only enables the `vector` and `pg_trgm` extensions.
