# Filing

How an analysed asset gets from `Input/` to `Content/` (specification sections 3, 4 and 7,
ADR-0005). The server is the single writer of `Content/` (invariant 2).

## Template language

A rule is a path template starting with `Content/`, at most five levels deep:

```
Content/{media_type}/{capture_date:%Y}/{capture_date:%Y-%m-%d}_{batch_label|Unsorted}/
```

| Syntax | Meaning |
| --- | --- |
| `{field}` | Value of the field. |
| `{field\|fallback}` | Literal fallback when the value is missing. |
| `{date_field:%fmt}` | `strftime` format for `capture_date` / `batch_hint_date` (default `%Y-%m-%d`). |

Fields: `media_type`, `capture_date`, `batch_label`, `batch_hint_date`, `batch_hint_location`,
`batch_hint_project`, `camera_model`, `field.<taxonomy-key>` (empty until the taxonomy exists in M4).
Allowed date directives: `Y y m d B b A a j U W G V u w e %`.
Fallback order: placeholder fallback, per-rule `fallbacks` (for example `{"capture_date": "undated"}`),
built-in (`undated` for dates, `unknown` otherwise). `capture_date` is the local date of the recording
(`capture_tz`, else UTC).

## Sanitising (ADR-0005)

Generated names only (originals keep their names): reserved characters and control characters become
`_`, leading dots become `_`, trailing dots/spaces are removed, device names (`CON`, `NUL`, `COM1` ...)
get a `_` prefix, NFC normalisation, at most 120 characters and 255 bytes per component. The path of
the longest file below the directory must fit `RUSHBASE_FILING_PATH_BUDGET` (default 180); the
longest components are shortened first. An existing folder that differs only in case or Unicode
normalisation is reused with its existing spelling (no `Video` next to `video`).

## Rule versions and API

Rules are immutable versions; exactly one is active. The default rule (version 1) is seeded on first
use. Activation affects only assets filed afterwards (stable paths).

| Call | Role | Purpose |
| --- | --- | --- |
| `GET /api/v1/filing-rules` | editor | List versions. |
| `POST /api/v1/filing-rules` | editor | New inactive version (`template`, `fallbacks`, `note`); 422 with the reason when invalid. |
| `POST /api/v1/filing-rules/dry-run` | editor | Paths of the newest `limit` assets (default 200) with warnings and conflicts (shortened/over-budget paths, adopted folder spellings). |
| `POST /api/v1/filing-rules/{id}/activate` | editor | Make the version active. |

## Moving

`finalize` runs on the server after all other stages. It never copies and never overwrites.

- A single asset: each file is renamed into the target directory under its original name. On a
  collision all files of the asset get the same `_2`, `_3` ... suffix on the stem so RAW/JPEG/XMP pairs
  stay together.
- A camera card structure (a directory kept intact): the asset waits in `review` until every asset in
  that directory has been analysed, is a duplicate or is quarantined; then the directory is renamed
  once (`<target>/<card dir>`, `_2` ... on collision) and the paths of all members are updated in
  one transaction. When files of other assets (duplicates, quarantined, unrelated files) lie in the
  directory, the members move file by file below a new directory of the same name, keeping the
  structure.
- `EACCES`/`EBUSY`/`EPERM` count as "in use": the job is retried with the queue's backoff; after the
  maximum number of attempts the asset is quarantined. A failure after some renames undoes them.

## Sidecars, XMP, journal

- `<dir>/_meta/<primary file name>.json` (schema `rushbase.sidecar/1`, `rushbase_core.sidecar`):
  asset id, files with hashes and original Input paths, normalized and raw metadata, shots, derived
  files, descriptive fields, tags placeholder, summaries (de/en), rule version, timestamps. For card
  structures `_meta` lies inside the card directory.
- Photos: `<stem>.xmp` next to the primary file (`dc:title`, `dc:description`, `dc:subject`,
  `xmp:Rating`); `<stem>.rushbase.xmp` when a camera XMP exists. Camera files are never changed.
- Optional central copy `.rushbase/meta/<asset-id>.json` (`PUT /api/v1/admin/library-settings`,
  `central_meta`).
- Every write is temp file + fsync + rename. Journal: table `move_journal` and
  `.rushbase/journal/YYYY-MM.jsonl` (one line per renamed file, or per renamed directory).

## Duplicates

After `hash`, and again before filing, an asset is a duplicate when every file hash occurs among the
files of one already filed asset. It gets `status = duplicate` and `duplicate_of`, its jobs are
cancelled and the files stay untouched in `Input/`. The statistics report their size as wasted
bytes; an admin can trash them later.

## Quarantine

A permanently failed job (retries exhausted or invalid output, for example a schema violation)
quarantines the asset. The next maintenance pass moves its files to
`.rushbase/quarantine/<asset-id>/<original relative path>` (the whole card directory if the asset is
its only member) and writes `error.json`. `GET /api/v1/quarantine` lists them;
`POST /api/v1/quarantine/{asset-id}/retry` moves everything back to the original location (409 if
occupied, nothing is moved) and creates a fresh job plan.

## Rebuilding

`rushbase-server rebuild-from-sidecars [--dry-run]` reads `Content/**/_meta/*.json` and
`.rushbase/meta/*.json` (the newest sidecar per asset wins) and recreates batches, assets, files,
metadata and shots idempotently. Jobs, stage results, audit log, users and rule history are not
restored. Exit code 1 when sidecars are invalid or paths conflict; missing files are reported.

## Derived files

`GET /api/v1/derived/{asset_id}/{path}` serves previews from `.rushbase/derived/<asset-id>/`
(authenticated, Range, `Cache-Control: private, max-age=86400`). Traversal, symlink escapes and
every path outside that directory answer 404; originals are not reachable. A sprite sheet is a
derived file of kind `other` named `sprite.<ext>` or `sprite_<tiles>.<ext>`.
