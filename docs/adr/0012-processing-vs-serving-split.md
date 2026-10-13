# ADR-0012: Processing on intermittent workers, serving on the server alone

- Status: Accepted
- Date: 2026-10-13

## Context
The typical deployment has a small always-on CPU-only server and a GPU workstation that is switched on only
occasionally. The owner's requirement: the initial (heavy) processing runs on that worker and handles all new
material as a batch whenever it is on; searching and everything else must work without the worker, on the server.

## Decision
1. **Worker = batch processor.** When a worker comes online it drains the backlog: it claims jobs stage by stage
   (all `probe`/`hash`/`preview`/`shots` first, then all `audio`, then all `vision`, …) to minimise model swaps,
   and keeps going until the queue is empty, a stop time is reached, or the user stops it. Leaving mid-job is safe
   (lease expiry, graceful release). `rushbase process [--until HH:MM] [--stages …] [--exit-when-empty]` and the
   one-click "Process" button in the companion app are the same code path.
2. **Server = serving.** Search (full-text + vector + facets), statistics, asset details, derived previews, needs
   lists, taxonomy, filing rules, MCP and the web UI depend only on PostgreSQL and `.rushbase/derived/`. No
   endpoint waits for or requires a worker.
3. **Query encoding on the server CPU.** The server loads only the *text* towers of the active embedding models
   (SigLIP 2 text int8, text-embedding int8) with ONNX Runtime CPU and encodes search queries itself. Models are
   downloaded once on the server with checksum verification. If they are unavailable, search degrades gracefully
   to full-text + facets (never fails). Model id and revision must match what the workers used to index; mismatches
   are reported and trigger re-index jobs, never silent mixing.
4. **Server-side stages:** only `finalize` (summary, sidecars/XMP, filing move). Optional cheap CPU stages
   (`probe`, `hash`) may be enabled on the server by configuration for setups without any worker, but are off by
   default.
5. Backlog visibility: the server always reports what is waiting (files, hours of material, per stage) so the
   companion app can show it immediately when the worker PC starts.

## Consequences
The worker can be off for days without affecting users. Server sizing is driven by PostgreSQL and query encoding
(target: query p95 < 500 ms on a 4-core CPU at 100 000 assets).
