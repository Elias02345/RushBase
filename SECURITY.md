# Security policy

## Supported versions

Rushbase is pre-1.0. Security fixes are applied to the latest release on `main` and to `dev`.

## Reporting a vulnerability

Please **do not open a public issue**. Report privately through GitHub's
["Report a vulnerability"](https://github.com/Elias02345/RushBase/security/advisories/new) form
on this repository. Include affected version or commit, reproduction steps and impact. You will
get an acknowledgement within a few days; coordinated disclosure is appreciated.

## Security principles

- No default secrets: the server refuses to start without `RUSHBASE_SECRET_KEY`; installers
  generate keys on first run. `.env` and keys are never committed.
- Originals are never modified or deleted by Rushbase; deleting is admin-only and goes to
  `.rushbase/trash/`.
- Least privilege for network shares: workers need read access plus write access to
  `.rushbase/derived/` only, where the storage permissions allow it.
- No telemetry. Network access beyond the local setup is limited to explicit model/backend
  downloads with checksum verification.
- Privacy by default: no face recognition; consent and "person identifiable" fields are part of
  the data model.
