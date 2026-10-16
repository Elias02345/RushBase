# Taxonomy

A controlled vocabulary of **fields** (location, activity, mood ...) with **values**. Fields are
`single` (one value per asset or shot) or `multi`.

## Values and statuses

- `proposed`: suggested by the AI (`classify` proposals) or by an editor; never used for search or
  filters, counted in `proposal_count` with up to five example assets.
- `approved`: part of the worker snapshot, search labels and facets.
- `deprecated`: hidden from snapshot and facets; existing tags stay.

Unique per field case-insensitively. Synonyms help workers map free text to the canonical value.

## Who may do what

| Action | Role |
|---|---|
| read vocabulary, flags | viewer |
| approve a proposal, propose a value | editor |
| create fields, create approved values, rename, relabel, synonyms, merge, deprecate, edit flags | admin |

Every change is written to the audit log (`taxonomy.*`).

## Versioning and re-classification

Changes to the **approved** vocabulary bump `taxonomy_version` (single-row table). The server then
queues a `classify` job (CPU, no GPU stage) for every asset whose latest classify result used an
older version. Switch off with the app setting `taxonomy.auto_reclassify = false`.

## Worker snapshot

`GET /api/v1/worker/taxonomy` (worker key):

```json
{"version": 3,
 "fields": [{"key": "location", "label_de": "Ort", "label_en": "Location", "kind": "single",
             "values": [{"value": "harbour", "synonyms": ["port"], "description": ""}]}],
 "flags": [{"key": "critical_scene", "description_de": "...", "description_en": "..."}]}
```

Approved values only. Flags are the review checks of the VLM, managed with `PUT /api/v1/taxonomy/flags`.

## Tags and precedence

`asset_tag(source = ai | claude | human)`; human beats claude beats ai. Reprocessing replaces only
`ai` rows, never touches `human`/`claude`, skips tags a person removed (remembered as rejected) and
does not add an AI tag for a single-valued field that already has a human or Claude value. Locked
assets are not modified by AI results at all.

## Facets and filters

`/api/v1/facets` returns technical facets, AI enums (shot size, camera angle, interior/exterior, time
of day, person identifiable, review state, consent) and one facet `tag.<field>` per taxonomy field
with approved values and counts. Filter with `tag=field:value` (repeatable; values of one field are
alternatives, fields combine with AND) or the enum query parameters.
