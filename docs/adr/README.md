# Architecture decision records

Decisions that shape Rushbase are recorded here, one file per decision, numbered in order of
acceptance. Copy [`0000-template.md`](0000-template.md) to start a new one. Fixed architecture
decisions (see `CLAUDE.md`) may only change through an ADR that supersedes the old one.

| # | Title | Status |
| --- | --- | --- |
| [0001](0001-tech-stack.md) | Technology stack, monorepo and workspaces | accepted |

Planned research ADRs (written before the affected milestone is built): DaVinci Resolve
scripting and import formats, RAW video decoders, vision-language model runtime, embedding
models, prebuilt inference backends, audio classifier, MCP authentication, Tauri specifics,
SMB behaviour.
