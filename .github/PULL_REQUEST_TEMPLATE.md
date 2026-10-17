## What and why

<!-- User-visible effect. Mention any impact on in-place updates (docs/UPDATE_RULES.md). -->

## Checklist

- [ ] Branched from `dev`, PR targets `dev`; Conventional Commit title
- [ ] `make lint typecheck test` pass locally (DB tests with `RUSHBASE_TEST_DATABASE_URL`)
- [ ] Tests added or updated
- [ ] Docs updated (`docs/`, `.env.example`, `THIRD_PARTY.md` where relevant)
- [ ] `CHANGELOG.md` updated for user-visible changes
- [ ] Migrations are additive, idempotent and have a working `downgrade()` (or: no migration)
- [ ] Architecture invariants in `CLAUDE.md` respected (originals untouched, single writer, no PyTorch)
- [ ] UI strings added to both `de.json` and `en.json` (or: no UI change)
- [ ] No secrets, internal hostnames or customer names
