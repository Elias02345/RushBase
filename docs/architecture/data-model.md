# Data model (milestone M1)

The PostgreSQL schema is defined by the SQLAlchemy models in
`server/src/rushbase_server/models/` and created by the additive Alembic migration
`0002_m1_schema.py`. A test (`test_models_match_the_migrations`) fails when the two drift apart.

## Conventions

- **Primary keys** are time-ordered UUIDv7 values (`rushbase_core.uuid7`), generated in Python and
  monotonic within a process. `ORDER BY id` therefore reproduces creation order; the queue uses it
  as the final tie-break. Every table has a single-column primary key. `audit_log` uses a UUIDv7 too.
- **Timestamps** are `timestamptz`; most tables carry `created_at` (and `updated_at` where rows change).
- **Paths** are stored relative to the library root with `/` separators and are kept exactly as found
  on the share. Identity comparisons use NFC + casefold keys (ADR-0005), never the raw string.
- **Enums** are `text` plus a named `CHECK` constraint (not PostgreSQL enum types), so new values can be
  added with a small migration. Values are mirrored in `rushbase_core` where code needs them.
- **JSONB** holds raw or extensible data (probe output, capabilities, stage output, settings).
- **Migrations** are additive only, guarded (`IF NOT EXISTS` / existence checks) and have a working
  `downgrade()` (see `docs/UPDATE_RULES.md`).

## Library and server

| Table | Purpose |
| --- | --- |
| `library` | Exactly one row (enforced by a unique expression index): `id` equals the id in `library.json`, `name`, `root_hint`, `schema_version`, `settings`. |
| `app_setting` | Runtime-adjustable key/value settings (`queue.paused`, `pipeline.stages`), `updated_by`. |

## Users and authentication

| Table | Purpose |
| --- | --- |
| `user` | `username` (`citext`, unique), `display_name`, `password_hash` (argon2id), `role` in `admin|editor|viewer`, `locale` in `de|en`, `disabled`, `last_login_at`. |
| `session` | Cookie session: `token_hash` (SHA-256 of the opaque cookie value), `csrf_token`, `expires_at`, `ip`, `user_agent`. |
| `api_token` | `kind` in `worker|personal`, `token_hash` (unique), `prefix` (first 8 characters, for display), `scopes text[]`, `last_used_at`, `revoked_at`; a worker token belongs to a worker, a personal token to a user (CHECK). |
| `audit_log` | Append-only: `at`, `actor_kind` in `user|worker|mcp|system`, `actor_id`, `action`, `target_kind`, `target_id`, `details`, `ip`. Indexed by `at DESC` and `(actor_kind, actor_id)`. |

## Workers

| Table | Purpose |
| --- | --- |
| `enrollment_token` | One-time token (hashed) with `expires_at`, `used_at`, `used_by_worker`, `created_by`, `note`. |
| `worker` | `name`, `hostname`, `platform`, `capabilities` (JSONB: `backends`, `vram_mb`, `ram_mb`, `stages`, `models`, `profile`), `version`, `status` in `online|offline|draining`, `last_seen_at`, `last_stage` (the stage of its previous claim, used for batching), `current_job_id`. |

## Ingest

| Table | Purpose |
| --- | --- |
| `batch` | A top-level folder of `Input/` (or a virtual per-day batch `Input/@auto/<day>` for loose files): `input_path` (unique), `label`, `hint_date`, `hint_location`, `hint_project`, `status` in `scanning|waiting|processing|done|error`, `priority`, `paused`, `first_seen_at` (the FIFO key), `completed_at`. |
| `input_file` | A file seen in `Input/`: `rel_path` (unique), `size`, `mtime`, `stable_count`, `last_changed_at`, `first_seen_at`, `last_seen_at`, `state` in `copying|stable|grouped|ignored|gone`, `quick_fp` (size + BLAKE3 of 3 x 64 KiB samples), `asset_id`. Ignored junk files (`._*`, `.DS_Store`, ...) are skipped and not recorded; the state `ignored` is reserved. |

## Assets

| Table | Purpose |
| --- | --- |
| `asset` | `batch_id`, `media_type` (`video|photo|audio|other`), `status` (`ingesting|processing|review|filed|quarantined|trashed|duplicate`), `duplicate_of`, `primary_file_id` (no FK, the two tables reference each other), `content_rel_dir` (set once when filed, never changed afterwards except by an admin migration), technical columns (`capture_at`, `capture_tz`, `duration_ms`, `width`, `height`, `fps`, `codec`, camera/lens, `focal_length_*`, `iso`, `log_profile`, `orientation`, `gps_lat/lon/alt`, `size_total`), descriptive columns (`title`, summaries, `rating` 0-5), privacy (`person_identifiable`, `consent`, `consent_ref` with `yes|no|unknown`), `locked`, `analysis_profile`, `search_tsv` (`tsvector`, GIN index; the maintaining trigger arrives with the search in M3), `filed_at`, `rule_version_id`. |
| `asset_file` | `role` in `primary|raw|jpeg|sidecar|proxy_camera|telemetry|audio_sync|member`, `rel_path` (unique; the current location, Input before filing, Content after), `original_rel_path` (immutable Input path), `size`, `mtime`, `blake3`, `quick_fp`, `card_structure_root` (a directory that must move as a unit). |
| `asset_metadata` | `normalized` and `raw` JSONB plus a `version` per asset. |
| `shot` | `(asset_id, idx)` unique; time range, keyframes, quality measures, descriptions, `search_tsv`. |

M4 adds `transcript_segment`, embedding tables (one per dimension, by ADR), taxonomy tables, M5 the
need lists, and collections arrive with the web UI.

## Results and queue

| Table | Purpose |
| --- | --- |
| `stage_result` | Append-only history of stage runs: `asset_id`, `job_id`, `stage`, `model_id`, `model_version`, `prompt_version`, `worker_id`, `started_at`, `finished_at`, `duration_ms`, `ok`, `output`, `error`. The latest successful row per `(asset, stage)` is current. A partial unique index allows at most one successful result per job. |
| `job` | `asset_id`, `stage` (`probe|hash|preview|shots|audio|embed|vision|classify|finalize`), `state` (`blocked|queued|leased|done|failed|cancelled`), `depends_on uuid[]`, `priority`, `enqueued_at` (the batch's `first_seen_at`), `attempts`, `max_attempts` (default 5), `not_before` (backoff), `lease_owner`, `lease_expires_at`, `last_error`, `requirements` (`{"gpu": bool, "min_vram_mb": int}`). |

Indexes: partial `ix_job_queue (stage, priority DESC, enqueued_at) WHERE state = 'queued'` and
`ix_job_lease_expires_at (lease_expires_at) WHERE state = 'leased'`.

### Job life cycle

```
blocked --(all dependencies done)--> queued --claim--> leased --result ok--> done
                                       ^                 |
                                       |   lease expiry / error: attempts + 1
                                       +---- backoff <---+--(attempts >= max_attempts)--> failed
                                                                (asset -> quarantined,
                                                                 other open jobs cancelled)
```

- **Plan:** `rushbase_core.pipeline.build_job_plan` builds the DAG per media type from the enabled
  stages. Disabled or inapplicable stages are not created; dependencies on them are replaced by their own
  dependencies. `probe` and `hash` have no dependencies (parallel); `finalize` is always present and runs on
  the server.
- **Order:** manual priority (per batch), then the stage the worker ran last (stage-batched draining,
  ADR-0012), then FIFO by batch first-seen time and asset creation (UUIDv7).
- **Leasing:** `SELECT ... FOR UPDATE OF job SKIP LOCKED`, filtered by the worker's stages, GPU
  availability and VRAM. Default lease 120 s, heartbeat every 30 s. Backoff after n failed attempts is
  `min(30 s * 2^(n-1), 1 h)`.
- **Quarantine:** after `max_attempts` the job fails and the asset becomes `quarantined`; the audit log
  records `asset.quarantine_intent`. The move into `.rushbase/quarantine/` is done by the filing service (M2).
- **Idempotency:** results of finished jobs are ignored (`duplicate: true`); a worker that lost its lease
  gets `409 lease_lost`.

## Organisation

| Table | Purpose |
| --- | --- |
| `filing_rule` | Versioned filing templates (`template`, `fallbacks`, `note`); a unique partial index allows one active rule. Used from M2. |
| `move_journal` | Database mirror of `.rushbase/journal/*.jsonl`: `asset_id`, `from_rel`, `to_rel`, `at`, `reason`. Used from M2. |

## Worker protocol (`/api/v1/worker/*`)

Bearer API key per worker (stored hashed, shown once at enrollment).

| Call | Purpose |
| --- | --- |
| `POST enroll` | Exchange a one-time enrollment token for an API key. |
| `GET library` | Library id, to compare with the worker's mounted `library.json`. |
| `POST claim` | Capabilities (optional) and `max_jobs`; returns jobs with file paths and upstream outputs. |
| `POST jobs/{id}/heartbeat` | Extend the lease. |
| `POST jobs/{id}/result` | `ok`/error, output JSON, model info, duration. Idempotent. |
| `POST jobs/{id}/release` | Graceful give-back without an attempt penalty. |

No endpoint waits for a worker; the backlog (`GET /api/v1/status`) is answered from the database alone.
