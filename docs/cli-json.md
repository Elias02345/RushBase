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
| 2 | Usage error (unknown option, invalid value); no JSON document is printed. |

## `rushbase version --json`

```json
{ "command": "version", "data": { "version": "0.1.0" }, "ok": true, "schema_version": 1 }
```

## `rushbase doctor --json`

Options: `--library PATH` (`RUSHBASE_LIBRARY_ROOT`), `--library-id UUID` (`RUSHBASE_LIBRARY_ID`),
`--server-url URL` (`RUSHBASE_SERVER_URL`).

`data.checks` is an ordered list: `ffmpeg`, `ffprobe`, `exiftool`, `library`, `server`.

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | string | Check identifier. |
| `status` | `"ok"` \| `"error"` \| `"skipped"` | `skipped` means the check was not configured. |
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
