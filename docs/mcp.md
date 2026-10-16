# Connect Rushbase to claude.ai

Rushbase ships a Model Context Protocol (MCP) server with its own OAuth 2.1 authorization server
(ADR-0010). claude.ai talks to it as a custom connector: Claude can search the library, read
details and keyframes, find gaps, keep needs lists, and (with your consent) tag assets and propose
vocabulary or filing rules. **No tool deletes or moves files** (architecture invariant 7); deleting
stays an admin action in the web UI.

## Requirements

- A public **HTTPS** address for the server, for example through Caddy, Nginx, a Cloudflare Tunnel
  or CloudGate. Examples are in `deploy/`.
- The paths **`/mcp`**, **`/.well-known/*`** and **`/oauth/*`** must reach Rushbase without a WAF
  rule, bot challenge, Cloudflare Access policy or extra login in front. claude.ai connects from
  Anthropic's servers (egress range `160.79.104.0/21`), so the web UI itself may stay protected.
- A Rushbase account with the **editor** or **admin** role to grant access (viewers cannot).

## Settings

| Setting | Meaning |
| --- | --- |
| `RUSHBASE_PUBLIC_URL` | Public origin, for example `https://media.example.org` (no path). **Required for MCP**; it is the audience of every token. Unset: `/mcp` answers 503 and `/health` lists the warning `mcp_public_url_missing`. |
| `RUSHBASE_MCP_ACCESS_TTL_SECONDS` | Access token lifetime (default 3600). |
| `RUSHBASE_MCP_REFRESH_TTL_DAYS` | Refresh token lifetime, sliding (default 30). |
| `RUSHBASE_MCP_RATE_LIMIT_PER_MINUTE` | Tool calls per OAuth client and minute (default 120). |
| `RUSHBASE_NEEDS_MATCH_THRESHOLD` | Minimum fused search score for an automatic needs-list candidate (default 0.012). |

Extra redirect URIs (besides claude.ai and loopback addresses) are managed under
**Administration > MCP / Claude**.

## Step by step in claude.ai

1. Set `RUSHBASE_PUBLIC_URL`, restart Rushbase and check **Administration > MCP / Claude**: it
   shows the connector URL `https://<host>/mcp`.
2. In claude.ai open **Settings > Connectors > Add custom connector**, enter
   `https://<host>/mcp` and confirm.
3. claude.ai opens the Rushbase consent page. It shows the client name, the redirect host and the
   requested scopes. Sign in with your Rushbase account and click **Allow**.
4. Ask Claude something about your library. Every call appears in the call log of the admin page.
5. To disconnect, revoke the client under **Administration > MCP / Claude** (or remove the
   connector in claude.ai).

## Scopes

| Scope | Allows |
| --- | --- |
| `read` | Search, details, keyframes, similar, facets, statistics, coverage, gap analysis, vocabulary and rules (read). |
| `annotate` | `annotate_asset`: tags (source `claude`), rating, note, lock, consent. Human edits always win over Claude, Claude over AI. |
| `taxonomy` | Propose values, approve, merge, add synonyms (merge and synonyms need the admin role). |
| `rules` | Store a filing rule with a dry run, activate it (applies to new material only). |
| `needs` | Needs lists: create, read, update, link candidates, close. |
| `processing` | `reprocess`: queue an analysis stage again. Only creates jobs. |
| `offline_access` | Keep the connection alive with refresh tokens. |

Tools also respect the role of the consenting account: a viewer-level account could not write even
with the scope.

## Needs lists

A needs list names footage you still need (items with a description, filters and a wanted count).
Rushbase attaches matching assets as **candidates** automatically after an asset is filed or
re-classified (hybrid search inside the item's filters, score above the threshold). It is passive:
nobody is notified. Review candidates on the **Needs** page or let Claude do it.

## Troubleshooting

- **claude.ai says it cannot connect / a 401 loop:** open `https://<host>/.well-known/oauth-protected-resource/mcp`
  in a browser; it must return JSON with `"resource": "https://<host>/mcp"`. If you see an HTML
  challenge page, the proxy or WAF is intercepting the path.
- **Redirect mismatch on the consent page:** the connector's callback is not on the allowlist.
  claude.ai (`https://claude.ai/api/mcp/auth_callback`, `https://claude.com/...`) and loopback
  addresses work out of the box; add other HTTPS callbacks in the admin page.
- **421 Misdirected Request / "Invalid Host header":** the proxy passes a different `Host` than
  `RUSHBASE_PUBLIC_URL`. Forward the original host (`proxy_set_header Host $host`).
- **503 on /mcp:** `RUSHBASE_PUBLIC_URL` is not set.
- **Tokens stop working after changing the URL:** tokens are bound to the old address; reconnect.
- **Refresh keeps failing:** a reused refresh token revokes its whole grant; reconnect.

## Verifying the endpoint

```sh
npx @modelcontextprotocol/inspector --cli https://<host>/mcp --transport http \
  --header "Authorization: Bearer <access token>" --method tools/list
```

Access tokens come from the OAuth flow; for a manual check use any OAuth client with PKCE S256.

## Tool reference

Generated from `core/src/rushbase_core/mcp_tools.py` with `scripts/gen-mcp-docs.py` (a test fails
when this section is stale).

<!-- tools:start -->
Results are JSON text of at most 30,000 characters. Lists are paginated: pass `next_cursor` back as `cursor`. Tools with scope `read` are read-only.

| Tool | Scope | Writes |
| --- | --- | --- |
| [`search_media`](#search_media) | `read` | no |
| [`get_asset`](#get_asset) | `read` | no |
| [`get_keyframes`](#get_keyframes) | `read` | no |
| [`find_similar`](#find_similar) | `read` | no |
| [`list_facets`](#list_facets) | `read` | no |
| [`facet_counts`](#facet_counts) | `read` | no |
| [`library_stats`](#library_stats) | `read` | no |
| [`coverage_report`](#coverage_report) | `read` | no |
| [`gap_analysis`](#gap_analysis) | `read` | no |
| [`needs_create`](#needs_create) | `needs` | yes |
| [`needs_list`](#needs_list) | `needs` | no |
| [`needs_get`](#needs_get) | `needs` | no |
| [`needs_update`](#needs_update) | `needs` | yes |
| [`needs_link_candidate`](#needs_link_candidate) | `needs` | yes |
| [`needs_close`](#needs_close) | `needs` | yes |
| [`annotate_asset`](#annotate_asset) | `annotate` | yes |
| [`taxonomy_list`](#taxonomy_list) | `read` | no |
| [`taxonomy_propose_value`](#taxonomy_propose_value) | `taxonomy` | yes |
| [`taxonomy_approve`](#taxonomy_approve) | `taxonomy` | yes |
| [`taxonomy_merge`](#taxonomy_merge) | `taxonomy` | yes |
| [`taxonomy_add_synonym`](#taxonomy_add_synonym) | `taxonomy` | yes |
| [`rules_get`](#rules_get) | `read` | no |
| [`rules_propose`](#rules_propose) | `rules` | yes |
| [`rules_activate`](#rules_activate) | `rules` | yes |
| [`processing_status`](#processing_status) | `read` | no |
| [`reprocess`](#reprocess) | `processing` | yes |

### search_media

Hybrid search (full text + semantic) over the media library. Returns assets or shots with summary, technical data and score; optional thumbnails. Paginated.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `q` | string | yes | Natural-language query. |
| `level` | `asset` | `shot` | no | `shot` returns scenes with timecodes. |
| `filters` | MediaFilters | no |  |
| `include_thumbnails` | boolean | no | Attach small JPEG thumbnails of the first hits (max 6). |
| `limit` | integer | no |  |
| `cursor` | string | no | Opaque cursor from `next_cursor` of a prior page. |

### get_asset

All data of one asset: technical metadata, AI summaries, tags with source, shots with descriptions, review state, paths, and a page of the transcript.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `asset_id` | string | yes | Asset UUID. |
| `transcript_offset` | integer | no | First transcript segment. |
| `transcript_limit` | integer | no |  |

### get_keyframes

Keyframe images (JPEG, at most 512 px, max 6) of an asset or one shot, with timecodes.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `asset_id` | string | yes | Asset UUID. |
| `shot_idx` | integer | no | Only this shot; default: first keyframe of each shot. |
| `limit` | integer | no |  |

### find_similar

Assets or shots that look like a given asset or shot (stored image vectors). Paginated.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `asset_id` | string | yes | Asset UUID. |
| `shot_idx` | integer | no | Compare a single shot. |
| `filters` | MediaFilters | no |  |
| `limit` | integer | no |  |
| `cursor` | string | no | Opaque cursor from `next_cursor` of a prior page. |

### list_facets

Available filter dimensions with value counts for the current filters/query (technical fields and taxonomy fields).

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `filters` | MediaFilters | no |  |
| `q` | string | no |  |

### facet_counts

Value counts of one facet under the given filters and query.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `facet` | string | yes | Facet key from `list_facets`. |
| `filters` | MediaFilters | no |  |
| `q` | string | no |  |

### library_stats

Library size: assets by type and status, storage, duration, capture year range.

No arguments.

### coverage_report

Matrix of asset counts for two fields (technical or taxonomy), optionally with the newest capture date per cell. Shows what the library covers and where it is thin.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `rows` | string | yes | Row dimension: technical (media_type, camera_model, lens_model, log_profile, orientation, codec, shot_size, camera_angle, interior_exterior, time_of_day) or a taxonomy field key. |
| `columns` | string | yes | Column dimension, same options as `rows`. |
| `filters` | MediaFilters | no |  |
| `include_last_capture` | boolean | no | Add the newest capture date per cell. |

### gap_analysis

For each wanted shot (description + filters + count) return the best matching library assets with scores, or `missing` when too few match.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `items` | list of GapItem | yes |  |
| `candidates` | integer | no | Top candidates per item. |

### needs_create

Create a needs list (what footage is still missing) with optional items.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `title` | string | yes |  |
| `purpose` | string | no |  |
| `items` | list of GapItem | no |  |

### needs_list

List needs lists with item counts. Paginated.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `status` | `open` | `closed` | no |  |
| `limit` | integer | no |  |
| `cursor` | string | no | Opaque cursor from `next_cursor` of a prior page. |

### needs_get

One needs list with items and candidates (auto-matched, Claude or human).

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `need_id` | string | yes |  |
| `include_thumbnails` | boolean | no |  |

### needs_update

Rename a needs list, add items, or change one item's status or wanted count.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `need_id` | string | yes |  |
| `title` | string | no |  |
| `purpose` | string | no |  |
| `add_items` | list of GapItem | no |  |
| `item_id` | string | no | Item to change (with the fields below). |
| `item_status` | `open` | `fulfilled` | `dropped` | no |  |
| `item_count` | integer | no |  |

### needs_link_candidate

Attach an asset (or shot) as candidate for a needs item, or confirm/reject it.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `item_id` | string | yes |  |
| `asset_id` | string | yes | Asset UUID. |
| `shot_idx` | integer | no |  |
| `status` | `candidate` | `confirmed` | `rejected` | no |  |

### needs_close

Close a needs list (or reopen it). Closed lists receive no new auto-matches.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `need_id` | string | yes |  |
| `reopen` | boolean | no | Reopen a closed list instead. |

### annotate_asset

Add or remove tags, set rating, note, lock or consent on an asset. Source `claude`; human edits always win over Claude, Claude wins over AI.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `asset_id` | string | yes | Asset UUID. |
| `add_tags` | list of string | no | `field:value` of approved values. |
| `remove_tags` | list of string | no |  |
| `rating` | integer | no |  |
| `note` | string | no | Free-text note. |
| `locked` | boolean | no | Protect from reprocessing. |
| `consent` | `yes` | `no` | `unknown` | no |  |
| `consent_ref` | string | no |  |

### taxonomy_list

The controlled vocabulary: fields, values, synonyms, status and asset counts.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `field` | string | no | Only this field key. |
| `status` | `proposed` | `approved` | `deprecated` | no |  |

### taxonomy_propose_value

Propose a new vocabulary value (status `proposed`, a person approves it).

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `field` | string | yes |  |
| `value` | string | yes |  |
| `label_de` | string | no |  |
| `label_en` | string | no |  |
| `synonyms` | list of string | no |  |
| `description` | string | no |  |

### taxonomy_approve

Approve a proposed value (editor or admin only).

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `value_id` | string | yes |  |

### taxonomy_merge

Merge one value into another of the same field (admin only). Tags move over.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `value_id` | string | yes | Value to fold in. |
| `target_id` | string | yes | Value that remains. |

### taxonomy_add_synonym

Add a synonym to a vocabulary value (admin only).

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `value_id` | string | yes |  |
| `synonym` | string | yes |  |

### rules_get

The active filing rule (template for the folder structure of new material) and history.

No arguments.

### rules_propose

Store a candidate filing rule (inactive) and return a dry run: where recent assets would be filed, conflicts and warnings. Nothing moves.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `template` | string | yes |  |
| `fallbacks` | object | no |  |
| `note` | string | no |  |
| `dry_run_limit` | integer | no |  |

### rules_activate

Activate a stored rule version. Applies to material filed afterwards only.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `rule_id` | string | yes |  |

### processing_status

Queue backlog per stage, online workers, asset counts by status, quarantine.

No arguments.

### reprocess

Queue an analysis stage again for given assets or a filter selection. Never touches or moves files.

| Argument | Type | Required | Description |
| --- | --- | --- | --- |
| `stage` | `probe` | `hash` | `preview` | `shots` | `audio` | `embed` | `vision` | `classify` | yes |  |
| `asset_ids` | list of string | no |  |
| `filters` | MediaFilters | no | Select by filter instead of ids (max 500 assets). |
<!-- tools:end -->
