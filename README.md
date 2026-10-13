# Rushbase

**Self-hosted, AI-assisted media library for video, photo and audio on your network storage.**

Your team drops raw footage ("rushes") into an `Input/` folder on a NAS. Rushbase detects the
files, lets workers analyse them (technical metadata, camera settings, speech, image content,
categories), files them permanently into a structured `Content/` folder with sidecar metadata,
and makes everything searchable - from a web UI, a companion app/CLI, local AI agents and Claude
via an MCP connector. Originals are never modified, moved after filing, or streamed through the
server.

## Why

- **Your storage, your data.** Everything runs on your own hardware. No telemetry, usable
  offline once models are downloaded.
- **Safe by construction.** Originals untouched, a single writer for `Content/`, stable paths
  (DaVinci Resolve projects keep working), the database rebuildable from sidecars.
- **Runs on what you have.** Workers use llama.cpp, whisper.cpp and ONNX Runtime - CPU, NVIDIA,
  AMD, Intel Arc and Apple Silicon, no PyTorch.
- **Humans and agents.** Web UI, desktop companion, scriptable CLI (`--json` everywhere) and an
  MCP server so Claude can see what exists, what is missing and plan shoots.

## Architecture

```
                ┌──────────────── Network storage (SMB) ─────────────────┐
                │  Input/   Content/   .rushbase/ (derived, backups, ...) │
                └───▲───────────▲──────────────▲───────────────▲─────────┘
                    │ mount     │ mount        │ mount         │ mount
          ┌─────────┴───┐  ┌────┴──────┐  ┌────┴─────────┐ ┌───┴────────────┐
          │   Server    │  │  Worker   │  │  Companion   │ │  DaVinci PCs   │
          │ Coordinator │◄─┤ headless  │  │  App (GUI)   │ │  (Resolve)     │
          │ DB, API,    │◄─┼───────────┼──┤ Processor +  │ └────────────────┘
          │ Web UI, MCP │  │ always-on │  │ Library      │
          └─────▲───────┘  └───────────┘  └──────────────┘
                │ HTTPS (reverse proxy / tunnel)
          claude.ai (MCP custom connector), browser
```

Server, workers and apps each mount the share themselves; only metadata, jobs and derived
previews travel over the network. Workers pull jobs from the server, so they need no open port.
Read `CLAUDE.md` for the full list of architecture invariants.

## Repository layout

| Path | Contents |
| --- | --- |
| `core/` | `rushbase-core`: library manifest, layout, path mapping, ignore rules |
| `server/` | `rushbase-server`: FastAPI coordinator, PostgreSQL access, migrations |
| `worker/` | `rushbase-worker`: worker engine and the `rushbase` CLI |
| `web/`, `packages/` | React web UI, shared UI components, generated API client |
| `desktop/` | Tauri companion app (planned, milestone M6) |
| `dev/` | Development stack (PostgreSQL + Samba) and synthetic test media generator |
| `docs/` | ADRs, status, update rules, CLI JSON contract |

## Quick start (development)

Requirements: Docker, `make`, `curl`, Node.js and `ffmpeg`. Python 3.12 and all packages are
installed into the project-local `.toolchain/` / `.venv/` - nothing system-wide.

```sh
make setup        # uv sync + pnpm install
make lint typecheck test
make testmedia    # synthetic test media in dev/library/Input
make dev          # PostgreSQL :15432, Samba :14450, server :18080, web :15173
make dev-down     # stops only the "rushbase-dev" compose project
```

Then open <http://127.0.0.1:15173> (landing page with live health), or:

```sh
curl -fsS http://127.0.0.1:18080/health
uv run rushbase doctor --library dev/library --server-url http://127.0.0.1:18080
```

## Status

Pre-release. Milestone M0 (foundation) is complete; see [`docs/STATUS.md`](docs/STATUS.md) for
the roadmap M1-M7 and the M0 demo. Configuration reference: [`.env.example`](.env.example).
Contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md). Security: [`SECURITY.md`](SECURITY.md).

## License

Apache License 2.0, see [`LICENSE`](LICENSE). Third-party components: [`THIRD_PARTY.md`](THIRD_PARTY.md).
