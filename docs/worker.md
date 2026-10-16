# Worker (milestone M2)

The worker is a headless CPU engine that pulls jobs from the server, runs stage handlers on files
it reads from its own mount of the library, and reports structured results (ADR-0012). The
server never waits for a worker. Result shapes are the contracts in
`core/src/rushbase_core/results.py`; the protocol is `server/src/rushbase_server/api/worker.py`.

## Architecture (`worker/src/rushbase_worker`)

| Module | Role |
| --- | --- |
| `config` | `WorkerSettings` (pydantic-settings): `RUSHBASE_*` env, `.env`, optional `worker.toml`. |
| `client` | httpx async client; retries with exponential backoff on network errors, 429, 502-504; maps 401/403 to `AuthError`, 409 to `LeaseLostError`. |
| `runner` | Claim loop: bounded concurrency, one heartbeat task per job, result submission, release of in-flight jobs on SIGINT/SIGTERM or stop time, `--exit-when-empty`. |
| `stages/` | One module per stage, each a `StageHandler` (`stage`, `requirements`, `async run(job, ctx) -> BaseModel`). Stages whose required tools are missing are not advertised. |
| `tools/` | Typed wrappers with timeouts for ffprobe, ffmpeg, ExifTool; tool lookup (setting, toolchain `bin`, PATH). |
| `probe/` | Mapping table (`mapping.py`), converters, sidecar XML parser, decode status (ADR-0003), normaliser. |
| `preview/`, `shots/` | Derived-file writers; pure analysis functions (motion, quality, segmentation). |
| `mount` | `verify_mount`: `library.json` id must equal the server's library id, else nothing is processed. |
| `state` | API key (mode 600), identity and last-run record in the data directory. |

Flow of `rushbase process`: load key, ask the server for the library id, verify the mount, then
claim up to `concurrency` jobs (stage-batched by the server), run them, heartbeat every
`heartbeat_seconds` (the server's value unless configured), submit results. A lost lease (409)
cancels the job and discards its result. Stop signals and `--until HH:MM` cancel running jobs and
release them without an attempt penalty. Failures are reported as `ok=false` with `retryable`
(`StageError(retryable=False)` for deterministic problems such as a corrupt photo).

## Stages

**probe** runs ffprobe and ExifTool concurrently (`-j -G1 -n -a -u -ee3`, so embedded telemetry is
read) and parses camera sidecar XML (any sibling `*.xml` with role `sidecar`, defused, 2 MiB cap).
Normalised fields come from `probe/mapping.py`: a tuple of `Rule(field, candidate keys, converter)`;
the first candidate that converts wins, so supporting a vendor means adding keys. Keys are
`Group:Tag`, bare `Tag`, `ffprobe:video.*|audio.*|format.*` and `sidecar:*` (element paths,
`item.<Name>`, `table.<Name>`). Notes: `capture_at` keeps its offset (`capture_tz` is `UTC` or
`+HH:MM`); times without an offset are floating (naive, `capture_tz=None`). `video.rotation` is the
clockwise display rotation (0/90/180/270); `orientation` accounts for it. `video.capture_fps` is
set only when it differs from `fps`. For photos `video` carries the (EXIF-oriented) pixel size and
`duration_ms` is `None`. `raw` holds the complete tool outputs (binary blobs and lists are cut).
Decode status: vendor RAW (R3D, BRAW, ARRIRAW, CRM) and ProRes RAW without the `prores_raw`
decoder (checked via `ffmpeg -decoders`) give `proxy_only` when an in-camera proxy with a matching
clip name exists, else `decoder_missing`; an unreadable container gives `metadata_only`; a codec
missing from the ffmpeg build gives `decoder_error`.

**hash** streams every file in 1 MiB chunks through BLAKE3.

**preview** writes below `.rushbase/derived/<asset-id>/` only (temp file `.<name>.rbtmp.<ext>` in
the same directory, then rename; stale temps are removed at job start):

| File | Content |
| --- | --- |
| `poster.webp` | frame at 10 % of the clip (max 10 s), 1280 px wide (photos: same width). |
| `sprite-10x1-320.jpg` | hover-scrub sprite: 10 tiles of 320 px width in one row; tile `i` shows the frame at `(i+0.5)/10 * duration`. The name encodes tiles and tile width. |
| `proxy.mp4` | H.264 (CRF 28, 720p max), AAC stereo, `+faststart`; `proxy_enabled=false` disables it. |
| `waveform.png`, `waveform.json` | transparent waveform image; peaks `{"schema":1,"duration_ms":..,"bins":N,"min":[..],"max":[..]}` (values -1..1). Audio-only assets get only these. |
| `preview.webp` | photos: up to 2048 px wide. Pillow (+ `pillow-heif` for HEIC); RAW/DNG via `rawpy` using the embedded thumbnail when it is at least 1000 px, else a half-size develop. RAW+JPEG pairs use the JPEG. |

`perceptual_hash` is a 64-bit dHash of the poster (16 hex digits). Log normalisation: `luts` maps a
log profile (`ExposureInfo.log_profile`, e.g. `slog3`, matched ignoring case and punctuation) to a
`.cube` LUT applied with `lut3d` to poster, sprite, proxy and keyframes; `normalization` is
`lut3d:<file>` or `null` when none is configured (the default).
Videos the probe marked undecodable without proxy yield an empty file list.

**shots** decodes once at reduced size (320 px wide, at most 25 fps; the 720p proxy is used when
the preview stage produced it) and feeds the frames to PySceneDetect's `AdaptiveDetector`
(0.6.6, `min_shot_seconds`), Farneback optical flow and the quality measures in one pass. Per
shot: three JPEG keyframes (`keyframes/shot-NNNN-{start,middle,end}.jpg`, slightly inside the
boundaries); `camera_motion` (static/pan/tilt/zoom/handheld/mixed, source `ai`, with confidence)
from the global flow series (median translation, radial divergence; velocities in px/s on a
160 px flow frame, thresholds in `shots/motion.py`); `sharpness` (1 - exp(-Laplacian variance /
200)), `shake` (high-frequency motion mapped to 0..1), `exposure` (median luma against 0.45,
-1..1), `noise` (Immerkaer estimate / 12). Values are relative to the reduced frame.

## CLI

All commands accept `--json`; see `docs/cli-json.md` for schemas and exit codes.

```sh
rushbase enroll --server https://rushbase.lan --token <one-time token> --name studio-pc
rushbase doctor
rushbase process [--stages probe,hash] [--until 07:00] [--exit-when-empty] [--concurrency 4]
rushbase worker run          # alias of process, used by deploy/systemd/rushbase-worker.service.in
rushbase status
rushbase analyze clip.mp4 [--stages probe,preview,shots] [--output-dir DIR] [--raw]
```

## Configuration

Environment variables (prefix `RUSHBASE_`), `.env` in the working directory, or
`worker.toml` (`$RUSHBASE_WORKER_CONFIG` or the per-platform config dir). Keys:

| Key | Default | Meaning |
| --- | --- | --- |
| `server_url` | from enrollment | Server base URL. |
| `library_root` / `library_roots.{linux,windows,macos}` | none | Local mount of the library root. |
| `library_id` | server's | Additional expected library id. |
| `data_dir` | platformdirs user data dir | API key (`api_key`, mode 600), `worker.json`, `last_run.json`. Set it explicitly for a systemd unit with `ProtectHome=true`. |
| `api_token` | none | Personal token; only `rushbase status` uses it (worker keys cannot read `/api/v1/status`). |
| `stages` | probe, hash, preview, shots | Stages to claim. |
| `concurrency` | CPU count / 2 | Parallel jobs. |
| `heartbeat_seconds`, `poll_interval_seconds`, `claim_batch` | server / 5 s / concurrency | Loop timing. |
| `ffmpeg`, `ffprobe`, `exiftool`, `toolchain_dir` | PATH / `$TOOLCHAIN_DIR/bin` | Tool locations. |
| `tool_timeout_seconds`, `long_tool_timeout_seconds` | 300 / 21600 | Timeouts (long: proxy, analysis). |
| `ffmpeg_hwaccel` | false | Add `-hwaccel auto` when decoding. |
| `poster_width`, `sprite_tiles`, `sprite_tile_width`, `proxy_enabled`, `proxy_height`, `proxy_crf`, `proxy_preset`, `photo_preview_width`, `waveform_*` | see `config.py` | Preview parameters. |
| `luts` | none | `{ slog3 = "/path/slog3.cube" }`. |
| `min_shot_seconds`, `analysis_width`, `analysis_max_fps`, `keyframe_width` | 0.6 / 320 / 25 / 960 | Shot analysis. |

## ExifTool

`python3 worker/scripts/fetch-exiftool.py` installs the version pinned in
`worker/binaries.lock.json` (SHA-256 from exiftool.org/checksums.txt, verified before extraction)
into `<toolchain>/exiftool/` and writes the wrapper `<toolchain>/bin/exiftool`. Perl must be
installed; the script and `rushbase doctor` fail clearly otherwise.

## Known limits

- Hashing a very large file in a thread cannot be interrupted; a stop waits for the current file.
- Camera-proxy matching uses the clip name only (no timecode/duration fallback yet).
- Vendor RAW decoders (ADR-0003 plugin protocol) are not implemented; such assets are metadata only.
