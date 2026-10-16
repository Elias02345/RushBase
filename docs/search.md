# Search

Search runs entirely on the server (ADR-0012). No worker has to be online; the only model inference
on the server is the encoding of the query text on the CPU.

## Channels

`GET /api/v1/search?q=...&level=asset|shot` fuses up to three channels with Reciprocal Rank Fusion
(`score = sum(1 / (60 + rank))`):

| Channel | Source | Needs |
|---|---|---|
| `fulltext` | PostgreSQL `search_tsv` (titles, file names, summaries, transcript, approved tag labels; for scenes the shot descriptions) | nothing |
| `transcript` | `transcript_segment.tsv`, mapped to the shot that contains the segment start (scene level) | nothing |
| `text` | multilingual-e5-base over `embedding_text` (asset summaries, shot descriptions, transcript chunks) | models on the server and vectors from `classify` |
| `image` | SigLIP 2 text tower against `embedding_image` (keyframes, photos) | models on the server and vectors from `embed` |

All filters (facets, media type, tags, review state, collection ...) are applied **in SQL inside
every channel before ranking**. Vector channels use the HNSW indexes with pgvector's iterative scan
(`hnsw.iterative_scan = relaxed_order`) so selective filters still return enough rows. Each channel
returns up to 200 candidates (more for deep pages, at most 1000); pagination is `offset`/`limit`
over the fused list. Per-channel timings are logged (`search` log record) and returned as
`channels_ms`; the p95 goal is below 500 ms.

`level=shot` returns scenes with `start_ms`/`end_ms`, a keyframe URL and the description; the UI
links them to the asset page at that timecode (`?t=<ms>`).

## Query encoders

`rushbase_server.search.encoder` loads two ONNX models with ONNX Runtime (CPU) and `tokenizers`:

- SigLIP 2 **text tower fp16** (`text-fp16`; int8 failed the fidelity bar, ADR-0011), lower-cased,
  padded or truncated to 64 tokens, 768 dimensions, L2-normalised;
- multilingual-e5-base (`model_quantized.onnx`), prefix `query: `, mean pooling, L2-normalised.

`RUSHBASE_MODELS_DIR` points at the worker's model store (`<data_dir>/models`), whose layout is
`<models_dir>/<model-id>/<file name>`. File names come from `worker/models.lock.json`. Model ids
default to the worker's (`RUSHBASE_SIGLIP_MODEL_ID`, `RUSHBASE_E5_MODEL_ID`).

## Compatibility of vectors

Vectors are only comparable inside one model. A stored vector is used when its `model_id` equals the
encoder's and, if a revision is configured, its `revision` too:

- e5: the revision is derived from the lock file (first 12 hex characters of the model file's
  SHA-256, exactly what the worker stores in `ModelRef.revision`);
- SigLIP: matched by model id only, because the worker's revision there depends on the vision
  precision it chose (fp16 or int8). Override with `RUSHBASE_SIGLIP_REVISION` / `RUSHBASE_E5_REVISION`.

Mismatching vectors are excluded and reported by `GET /api/v1/search/capabilities`
(`vectors_mismatched`, `reembed_needed`). The admin action `POST /api/v1/admin/reembed` (Administration,
tab Search) queues `embed` + `classify` for the affected assets.

## Degradation

Search never fails because of missing models. Without `RUSHBASE_MODELS_DIR`, with missing files or
without compatible vectors the semantic channels are skipped: the response lists them in `skipped`
with the reason, the UI shows a note, and full-text with all facets keeps working.

## Find similar

`GET /api/v1/assets/{id}/similar` and `GET /api/v1/shots/{id}/similar` use the **stored** image
vectors (average of the item's keyframes, cosine distance), so no encoder is involved. Search by an
uploaded example image needs the vision tower, which only workers run (ADR-0012);
`POST /api/v1/search/by-image` answers 501 with that explanation.

## Tests

`FakeEncoder` (hash-based bag-of-words vectors) drives the unit and API tests. With real models
present, `RUSHBASE_TEST_MODELS_DIR=<models dir> uv run pytest server/tests/test_encoder_real.py`
checks the real towers.
