# ADR-0010: MCP server and OAuth for claude.ai custom connectors

- Status: Accepted
- Date: 2026-10-13

## Context
claude.ai custom connectors support OAuth with DCR (RFC 7591) and CIMD, always use PKCE S256, discover the
authorization server via a 401 `WWW-Authenticate: Bearer resource_metadata=...` and Protected Resource Metadata
(RFC 9728, `resource` must equal the entered MCP URL exactly, only the first `authorization_servers` entry is used),
callbacks `https://claude.ai/api/mcp/auth_callback` (possibly `https://claude.com/...`), refresh on 401 and before
expiry, endpoints must answer within 10 s, egress range `160.79.104.0/21`. Tool results ≲150 000 characters,
≤240 s per call. Current MCP revision 2026-07-28; Claude documents the 2025-03-26/06-18/11-25 auth specs.
Python SDK `mcp` 2.3.0 (`MCPServer`, `streamable_http_app()`, `mcp.server.auth`), v1 line 1.30.0.

## Decision
1. Rushbase ships its own OAuth 2.1 authorization server in the FastAPI process (`/oauth/*`,
   `/.well-known/oauth-authorization-server`), consent page with Rushbase login. No external IdP required.
2. Metadata: `code_challenge_methods_supported: [S256]`, grants `authorization_code`, `refresh_token`,
   `token_endpoint_auth_methods_supported: [none]`, DCR `registration_endpoint`, CIMD
   (`client_id_metadata_document_supported: true`) with an SSRF-safe fetcher (https only, no private ranges, size
   and time limits). Scopes: `read annotate taxonomy rules needs processing` + `offline_access`.
3. Redirect URIs: exact-match allowlist `https://claude.ai/api/mcp/auth_callback`,
   `https://claude.com/api/mcp/auth_callback`, plus loopback `http://127.0.0.1:*` / `http://localhost:*`.
4. `/mcp` is a resource server: Streamable HTTP, 401 with `resource_metadata` + `scope`, audience validation
   (RFC 8707 `resource`), `transport_security` allowlist with the public hostname.
5. Tokens: opaque, stored hashed; access 1 h; refresh rotated on use with reuse detection; `invalid_grant` on bad
   refresh. Every MCP call is audit-logged; rate limits per client.
6. SDK: `mcp` pinned exactly (2.3.0; fall back to 1.30.0 only via a follow-up ADR).
7. Tool results target ≤ 30 000 characters (fits Claude Code too), always paginated; long work returns job ids.
   Input schemas kept small. MCP has no tool to delete files or move existing Content.

## Consequences
We own security-critical OAuth code → dedicated tests (PKCE, redirect matching, audience, refresh rotation, CIMD SSRF)
and a security review in M7. Real connector test needs a public HTTPS URL (operator task).

## Sources
- https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- https://claude.com/docs/connectors/building/authentication
- https://py.sdk.modelcontextprotocol.io/run/authorization/
- https://pypi.org/project/mcp/
