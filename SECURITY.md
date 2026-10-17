# Security policy

## Supported versions

Rushbase is pre-1.0. Security fixes are applied to the latest release on `main` and to `dev`.

## Reporting a vulnerability

Please **do not open a public issue**. Report privately through GitHub's
["Report a vulnerability"](https://github.com/Elias02345/RushBase/security/advisories/new) form
on this repository. Include affected version or commit, reproduction steps and impact. You will
get an acknowledgement within a few days; coordinated disclosure is appreciated.

## Threat model (summary)

| Attacker | Capability | Main defences |
| --- | --- | --- |
| **Share-write attacker** | Can write to the library share (`Input/`, `Content/`, `.rushbase/`), e.g. a compromised client or guest account. | Derived files are served by extension allow-list with `Content-Security-Policy: sandbox` (no stored XSS from planted `.html`/`.svg`); names with backslashes or control characters are never ingested; hard-link/`renameat2` no-overwrite moves and exclusive, non-following temp files; symlinked sidecars are ignored; ffmpeg/ffprobe get `-protocol_whitelist file,pipe`, `file:` inputs and playlist/script files (HLS, DASH, concat) are refused; Pillow pixel cap; model downloads are re-verified against pinned SHA-256; units run sandboxed and SMB files are created `0660`. |
| **Network attacker** | Reaches the HTTP port or the public hostname. | No default secrets; hashed tokens and sessions, CSRF, login/setup/OAuth rate limits, `RUSHBASE_TRUSTED_PROXIES` for client addresses, one-time setup token, metrics token, request size limit, security headers (CSP, framing, referrer, nosniff) on every response, `Secure` cookies derived from the public URL, read-only personal API tokens (`scopes: ["read"]`). |
| **Malicious OAuth/MCP client** | Registers itself (DCR or metadata document) and tricks a user into consenting. | Redirect allow-list, PKCE S256, audience binding, SSRF-safe metadata fetch with negative cache and row caps, per-IP and per-client rate limits, registrations capped per hour, consent page marks unverified clients and shows the redirect host, `rules_activate` needs an admin account, MCP never deletes or moves files, issuance/refresh/revocation and rejected tokens are audited. |
| **Prompt injection via media** | Text inside footage, transcripts, tags or file names tries to steer an AI client reading Rushbase output. | Rushbase makes no decisions and sends nothing (invariant 8); MCP tools return data only, are scope-gated and write only annotations marked `source: claude`; structured worker results are schema-validated and size-bounded. Treat all returned text as untrusted data in your client. |

Hardening options: set `RUSHBASE_SETUP_TOKEN` (installer default), `RUSHBASE_TRUSTED_PROXIES`,
`RUSHBASE_PUBLIC_URL` with HTTPS, a metrics token if you scrape, read-only personal tokens for
scripts, and mount the share with the 0660/0770 modes of `docs/operations.md`. Update signature
enforcement (`RUSHBASE_UPDATE_REQUIRE_SIGNED`) is still off by default.

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
