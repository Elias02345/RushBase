# Release checklist (v1.0)

Every item is either checked with evidence (link, report, CI run) or explicitly waived with a
reason. Automated items are re-verified on the release commit; manual items are signed off by
the maintainer.

## Blockers

- [ ] **Signed releases.** Maintainer SSH signing key registered on GitHub, `deploy/allowed_signers`
      shipped, release tag signed, `RUSHBASE_UPDATE_REQUIRE_SIGNED=true` default for the `main`
      train (security review M3, `SECURITY.md`).
- [ ] **Desktop updater key.** `tauri signer` key pair generated, public key set as repository
      variable `RUSHBASE_UPDATER_PUBKEY`, private key + password as secrets, offline backup made
      (`docs/companion.md`). Without it, installers ship without the auto-updater.
- [ ] **claude.ai connector tested for real** against a public HTTPS deployment (OAuth consent,
      tool calls, refresh after 1 h) — `docs/mcp.md`.

## Automated (CI on the release commit)

- [x] Lint, types, unit + integration tests (`CI` workflow: python, web, desktop-rust, shell).
- [x] Mock E2E with axe accessibility checks (`e2e` job).
- [x] Installer / update / rollback / watchdog / doctor sandbox (`sandbox` job, systemd container).
- [ ] Desktop installers build for Windows, macOS (universal) and Linux (`Desktop` workflow).
- [ ] whisper.cpp GPU binaries built and pinned in `worker/binaries.lock.json` (`whisper-build`).
- [x] Worker container images build (cpu, cuda, vulkan, rocm).

## Verified locally on the reference machines

- [x] Real-stack E2E (`make e2e-real`): setup → enroll → process → search → proxy playback.
- [x] All AI stages end to end on an 8 GB GPU (`docs/eval/2026-10-vlm-benchmark.md`).
- [x] Scale test at 100 000 assets / 50 TB (`docs/eval/2026-10-perf.md`): list, facets, detail,
      unfiltered hybrid search p95 < 500 ms on 4 cores. **Known gap:** filtered hybrid search
      p95 ≈ 800 ms with an 8 GB database host (cold pages); meets the budget warm or with
      16 GB RAM — documented sizing in `docs/operations.md`.
- [x] Security review and fixes (`SECURITY.md`, CHANGELOG "Security").
- [x] Robustness: worker killed mid-job (`scripts/kill-test.sh`), server restart during
      processing, library mount lost and restored.
- [x] Disaster-recovery drill: fresh server + existing library → restore from the newest backup
      in `.rushbase/backups/` → search works; and without backups → `rebuild-from-sidecars`.
      `scripts/dr-drill.sh` PASS (16 checks), nightly in CI (`dr-drill` job); procedure and what
      is not recoverable: `docs/operations.md` "Disaster recovery".

## Manual test matrix (maintainer)

- [ ] Companion app: Linux + NVIDIA (reference), Windows + NVIDIA/AMD/Intel, macOS Apple
      Silicon, CPU-only — first run, model download, "Verarbeiten", library playback.
- [ ] DaVinci Resolve Studio: `rushbase resolve selftest --project rushbase-test`, then
      "An Resolve senden" (bins, metadata, markers) on a throwaway project.
- [ ] Free Resolve: FCPXML 1.10, metadata CSV and EDL import.
- [ ] Real footage golden set (≥ 100 keyframes) labelled; `rushbase-server eval` report
      committed to `docs/eval/`; `gpu8` default VLM confirmed or switched.

## Documentation

- [x] Operations (install, update, rollback, proxy/tunnel, hardening): `docs/operations.md`.
- [x] Connector guide: `docs/mcp.md`. Companion: `docs/companion.md`. Worker: `docs/worker*.md`.
- [ ] End-user guide (German + English): searching, reviewing, tagging, needs lists, sending to
      Resolve.
- [ ] `CHANGELOG.md` release section, `THIRD_PARTY.md` complete, version bumped everywhere.
