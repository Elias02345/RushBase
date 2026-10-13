# ADR-0002: DaVinci Resolve integration

- Status: Accepted
- Date: 2026-10-13

## Context
The companion app must push selected material into DaVinci Resolve (import, bins, clip metadata,
markers at shot boundaries, clip colours) and optionally read back "used in project". Verified against
the Resolve Studio 21.1.1 scripting README and `DaVinciResolveScript.pyi` stubs:

- External scripting is a Studio feature (Preferences > System > General: None / Local / Network,
  network port 1144). Since 21.1 Blackmagic moved Python scripting to Studio entirely; the free edition
  cannot be scripted from Python.
- 21.1+ ships its own interpreter (`ResolvePython`): Linux `/opt/resolve/bin/ResolvePython`, Windows
  `C:\Program Files\Blackmagic Design\DaVinci Resolve\ResolvePython\ResolvePython.exe`, macOS
  `/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/ResolvePython`.
  Older versions need an external Python with `RESOLVE_SCRIPT_API`, `RESOLVE_SCRIPT_LIB` and
  `PYTHONPATH+=$RESOLVE_SCRIPT_API/Modules/` set per OS (paths documented in the Resolve README).
- Relevant API: `MediaPool.AddSubFolder`, `SetCurrentFolder`, `ImportMedia([{"FilePath": p}])`,
  `MediaPoolItem.SetMetadata({...})`, `GetMetadata()`, `SetThirdPartyMetadata({...})` (19.0.2+),
  `AddMarker(frameId, color, name, note, duration, customData)`, `SetClipColor(name)`,
  `GetClipProperty()` (incl. `File Path`, `Usage`), `MediaPool.ExportMetadata(file, clips)`.
  Marker colours and clip colours are different sets.
- Timeline export constants in 21.1.1: FCPXML 1.8/1.9/1.10, EDL, OTIO, ALE. Import accepts FCPXML, EDL,
  OTIO, AAF, XML, DRT, ADL.
- UNVERIFIED: exact `SetMetadata` key names (Description, Keywords, Comments, Scene, Shot, Take,
  Good Take), metadata CSV header names, FCPXML ≥1.11 import.

## Decision
1. Live push ("Send to Resolve") is offered only when Resolve **Studio** is detected and reachable.
   It is a small Python bridge executed with `ResolvePython` (21.1+) or a configured Python with the
   env vars above. It is not frozen into a sidecar (fusionscript must match the interpreter).
2. Startup probe: create a scratch bin, set and read back one metadata key, and cache the key names
   reported by `GetMetadata()` — protects against key-name drift between versions.
3. Pushes are idempotent: existing pool items are matched by `File Path`; the Rushbase asset id is
   stored in third-party metadata and in marker `customData`.
4. Every API call's return value is checked; failures surface as "Resolve Studio not reachable /
   call failed", never silently. Writing into the open project requires explicit user confirmation.
5. File export works without scripting (also for the free edition): metadata CSV (header template
   captured once via `ExportMetadata` and committed as a fixture), FCPXML **1.10** (1.9 optional),
   CMX 3600 EDL for simple cut lists.
6. "Used in project" read-back via `GetClipProperty()["Usage"]` (Studio only).

## Consequences
- Free-edition users get file exports and drag-and-drop, but no live push.
- The scripting surface changes often; the bridge is tested against a pinned Resolve range and the
  probe detects drift at runtime.

## Alternatives considered
- File exports only: rejected as main path (metadata/markers less complete).
- Network scripting (port 1144): possible but Local is the default for security.
- Workflow Integration plugin (Electron): out of scope.

## Sources
- Resolve Studio 21.1.1 scripting README and `.pyi` stubs (local installation)
- https://www.pugetsystems.com/blog/2026/09/10/how-davinci-resolve-free-v21-1-scripting-changes-affect-puget-bench/
- https://docs.ayon.dev/docs/addon_resolve_admin
- https://forum.blackmagicdesign.com/viewtopic.php?p=806873
- https://kb.pomfort.com/silverstack/hands-on/creating-dailies/export-to-davinci-resolve/
