# ADR-0007: Embeddings via ONNX Runtime

- Status: Accepted (amended by ADR-0011)
- Date: 2026-10-13

## Context
Two retrieval spaces are needed: cross-modal text→image (keyframes, photos) and text→text (descriptions,
transcripts), in German and English, without PyTorch. The server (no GPU) must embed queries itself.

## Decision
1. Cross-modal: `onnx-community/siglip2-base-patch16-256-ONNX` (Apache-2.0) — vision tower fp16 (GPU) / int8
   (CPU), text tower int8; **768 dims**, L2-normalised. Preprocessing: 256×256 bilinear, rescale 1/255,
   mean/std 0.5. Text lowercased, padded/truncated to 64 tokens. Optional high profile: so400m-patch14-384 (1152).
2. Text: `Xenova/multilingual-e5-base` `onnx/model_quantized.onnx` (MIT), **768 dims**, prefixes `query: ` /
   `passage: `, mean pooling + L2 norm. Options: e5-small (384), bge-m3 int8 (1024).
3. Tokenisation via the `tokenizers` package (`Tokenizer.from_file`), no `transformers` at runtime.
4. Storage: one table per embedding space (`embedding_image_768`, `embedding_text_768`) with `model_id` and
   `model_revision` columns, HNSW `vector_cosine_ops`. A model change means a new column/table and re-indexing,
   never mixing spaces.
5. Search fuses full-text, e5 (on the VLM's German and English descriptions) and SigLIP2 scores with Reciprocal
   Rank Fusion — German SigLIP2 queries are weaker than English, the e5 channel compensates.
6. Golden vector fixtures (generated once with reference implementations) guard against preprocessing drift
   (cosine ≥ 0.99 fp16, ≥ 0.97 int8).

## Consequences
≈ 650 MB model download. Query latency on a 4-core CPU estimated < 100 ms (benchmark in M4/M7).

## Sources
- https://huggingface.co/onnx-community/siglip2-base-patch16-256-ONNX
- https://arxiv.org/abs/2502.14786
- https://huggingface.co/Xenova/multilingual-e5-base
