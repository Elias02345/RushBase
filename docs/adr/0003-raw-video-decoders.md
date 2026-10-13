# ADR-0003: RAW video decoding via optional plugins

- Status: Accepted
- Date: 2026-10-13

## Context
Previews and analysis need decoded frames from camera RAW formats. Licensing findings:

- **R3D SDK**: royalty-free click-through licence; redistributables only "in original form" as part of
  a developer program, under an EULA restricting copying/sublicensing; headers, docs and static libs
  must not be distributed. Incompatible with bundling in an Apache-2.0 project.
- **Blackmagic RAW SDK** (6.0, Sep 2026): free download; redistribution terms of the runtime
  UNVERIFIED → treated as not allowed.
- **ProRes RAW**: FFmpeg 8.0 (2025-08) added the `prores_raw` decoder (CPU) plus an optional Vulkan
  hwaccel. Vulkan is not required.
- **ARRIRAW**: format unencrypted; SDK terms UNVERIFIED.
- **CinemaDNG**: per-frame DNG, decodable with LibRaw (`rawpy`).
- Metadata without SDK: ExifTool has a Red module (R3D v1/v2); BRAW/ARRI coverage via generic
  QuickTime tags only (UNVERIFIED).

## Decision
1. Rushbase never bundles, packages or downloads a vendor SDK. Users install vendor software
   themselves; Rushbase loads it from a user-configured path.
2. Decoders are plugins discovered via the Python entry-point group `rushbase.decoders`, with a manifest
   (`formats`, `capabilities`: probe/thumbnail/frame/proxy, `requires`). Plugins run as subprocesses
   speaking a small versioned JSON protocol over stdin/stdout, so a crash cannot take down the worker.
3. Built in: FFmpeg (ProRes RAW when `ffmpeg -decoders` lists `prores_raw` — detected by capability,
   not version) and CinemaDNG/DNG via `rawpy`.
4. Fallback chain when no decoder is available: metadata-only extraction (ExifTool + ffprobe) →
   match in-camera proxies by clip name, then timecode/duration → use the proxy for previews.
5. Explicit decode status per asset: `ok | proxy_only | metadata_only | decoder_missing |
   decoder_error`; the UI shows which plugin would fix `decoder_missing`.

## Consequences
- No licence exposure; RAW files are still catalogued and searchable without decoders.
- The plugin protocol is a public API and is versioned.

## Alternatives considered
Bundling SDKs (licence), reverse-engineered decoders (none available for BRAW/R3D), mandatory Vulkan.

## Sources
- https://www.reddigitalcinema.com/legal/red-r3d-sdk-license-agreement
- https://www.blackmagicdesign.com/developer/products/braw/sdk-and-software
- https://raw.githubusercontent.com/FFmpeg/FFmpeg/release/8.0/Changelog
- https://ffmpeg.org/doxygen/8.0/prores__raw_8c.html
- https://exiftool.org/TagNames/index.html
