# CLI JSON output contract

Every `rushbase` command accepts `--json`. With it, the command prints exactly **one JSON
object on a single line to stdout** and nothing else on stdout; diagnostics go to stderr. The
schema is versioned: additive changes keep `schema_version`, breaking changes increment it.

## Envelope

```json
{ "schema_version": 1, "command": "<name>", "ok": true, "data": { } }
```

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer | Version of this contract (currently `1`). |
| `command` | string | The command that produced the document. |
| `ok` | boolean | `true` when the command succeeded; mirrors a zero exit code. |
| `data` | object | Command specific payload, documented below. |

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success (`ok: true`). |
| 1 | The command ran but reported a failure (`ok: false`), e.g. a failed doctor check. |
| 2 | Usage or configuration error (unknown option, invalid value, bad `--stages`/`--until`). |
| 3 | `process`: the local library mount does not match the server's library. |
| 4 | Server unreachable, enrollment token rejected, or API key rejected. |
| 5 | `process`: the worker is not enrolled. |

With `--json`, failures with exit codes 1 and 3-5 print `{"ok": false, "data": {"error": "..."}}`.

## `rushbase version --json`

```json
{ "command": "version", "data": { "version": "0.1.0" }, "ok": true, "schema_version": 1 }
```

## `rushbase doctor --json`

Options: `--library PATH` (`RUSHBASE_LIBRARY_ROOT`), `--library-id UUID` (`RUSHBASE_LIBRARY_ID`),
`--server-url URL` (`RUSHBASE_SERVER_URL`).

`data.checks` is an ordered list: `ffmpeg`, `ffprobe`, `exiftool`, `library`, `server`, `perl`, `ffmpeg_encoders`, `decoder_prores_raw`, `data_dir`, `enrollment`, `mount` (a check only appears when applicable; new checks are additive).

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | string | Check identifier. |
| `status` | `"ok"` \| `"warn"` \| `"error"` \| `"skipped"` | `skipped`: not configured; `warn`: works, but a feature is limited (not enrolled, no `prores_raw`). |
| `detail` | string | Human readable explanation (version string, path or error). |

`ok` is `false` (exit code 1) if any check has status `error`.

```json
{
  "command": "doctor",
  "data": {
    "checks": [
      { "detail": "ffmpeg version 7.1", "name": "ffmpeg", "status": "ok" },
      { "detail": "no server URL configured (--server-url)", "name": "server", "status": "skipped" }
    ]
  },
  "ok": true,
  "schema_version": 1
}
```

## `rushbase enroll --server URL --token T --name N --json`

`data`: `worker_id`, `name`, `server_url`, `library_id`, `enrolled_at`, `api_key_file`. The API key
itself is never printed.

## `rushbase process [--stages S] [--until HH:MM] [--exit-when-empty] [--concurrency N] --json`

(`rushbase worker run` is an alias with `command: "worker run"`.) Exception to the single-document
rule: prints **JSON lines**, one envelope per event, `data.event` being one of `started`,
`claimed`, `job_started`, `job_done`, `job_failed`, `job_released`, `lease_lost`, `claim_failed`,
`report_failed`, `stopped`. `ok` is `false` for `job_failed`, `claim_failed`, `report_failed`.
Common fields: `job_id`, `stage`, `asset_id`, `duration_ms`, `error`, `retryable`. The last line
(`stopped`) carries `reason` (`empty`, `stop_time`, `sigint`, `sigterm`, `stopped`) and the counters
`completed`, `failed`, `released`, `lease_lost`, `by_stage`. Exit code 0 also when jobs failed
(failures belong to the server's retry logic); SIGINT/SIGTERM stop gracefully with exit code 0.

## `rushbase status --json`

`data`: `enrolled`, `worker` (identity or null), `last_run` (or null), `server`
(`{url, reachable, health}` or `{url, reachable: false, error}`), `backlog` (the server's
`/api/v1/status` document, read with the worker key (`/api/v1/worker/status`) or `RUSHBASE_API_TOKEN`; null with `backlog_note` when neither is available).
Exit code 4 when the server is configured but unreachable.

## `rushbase analyze FILE [--stages S] [--output-dir DIR] [--raw] --json`

Runs probe/preview/shots locally. `data`: `file`, `media_type`, `asset_id`, `output_dir`,
`stages`: map stage name to `{ok, duration_ms, output | error | skipped}`; `output` is the stage
contract JSON (`ProbeOutput` etc.), `probe.raw` only with `--raw`. Derived paths in the output
are relative to `output_dir`. Exit code 1 if a stage failed.
