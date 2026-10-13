# Architecture decision records

Decisions that shape Rushbase are recorded here, one file per decision, numbered in order of
acceptance. Copy [`0000-template.md`](0000-template.md) to start a new one. Fixed architecture
decisions (see `CLAUDE.md`) may only change through an ADR that supersedes the old one.

| # | Title | Status |
| --- | --- | --- |
| [0001](0001-tech-stack.md) | Technology stack, monorepo and workspaces | accepted |
| [0002](0002-davinci-resolve-integration.md) | DaVinci Resolve integration | accepted |
| [0003](0003-raw-video-decoders.md) | RAW video decoding via optional plugins | accepted |
| [0004](0004-tauri-companion.md) | Tauri 2 companion app specifics | accepted |
| [0005](0005-smb-semantics.md) | Working with SMB shares | accepted |
| [0006](0006-vision-language-model.md) | Vision-language model via llama.cpp | accepted |
| [0007](0007-embeddings.md) | Embeddings via ONNX Runtime | accepted |
| [0008](0008-inference-binaries.md) | Prebuilt inference binaries, pinning and verification | accepted |
| [0009](0009-audio-events.md) | Audio event classification | accepted |
| [0010](0010-mcp-auth.md) | MCP server and OAuth for claude.ai | accepted |
| [0011](0011-model-refresh-2026-10.md) | Model refresh (October 2026) | accepted |
| [0012](0012-processing-vs-serving-split.md) | Processing on workers, serving on the server | accepted |
