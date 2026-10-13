# ADR-0011: Model refresh (October 2026)

- Status: Accepted — amends ADR-0006, ADR-0007, ADR-0008 and ADR-0009
- Date: 2026-10-13

## Context
A second research pass compared models released between late 2025 and October 2026 against the choices in
ADR-0006–0009, under the same hard constraints: no PyTorch at runtime (llama.cpp / whisper.cpp / ONNX Runtime),
permissive licence, fits an 8 GB GPU with three keyframes per request, good German.

Key findings (official model cards):
- **Qwen3.5** (Feb 2026, Apache-2.0, natively multimodal, hybrid Gated-DeltaNet attention → small KV cache).
  Qwen3.5-4B: MMMU-Pro 66.3, OCRBench 85.0, VideoMME (w/o sub) 76.9, CountBench 96.3 — above Qwen3-VL-30B-A3B
  on the same card. Qwen3.5-9B: 70.1 / 89.2 / 78.4. GGUF + mmproj via `unsloth/Qwen3.5-{2B,4B,9B}-GGUF`
  (4B Q8_0 4.48 GB + mmproj F16 0.67 GB; 9B Q4_K_M 5.68 GB + mmproj 0.92 GB). Thinking is on by default.
  Card scores may be thinking-mode (UNVERIFIED); we run non-thinking.
- Gemma 4 E4B is clearly weaker on vision; Gemma 4 12B does not fit 8 GB with images. MiniCPM-V 4.6/4.7 (≈1 B,
  Apache-2.0) is the best CPU-class option.
- ASR: no model is clearly more accurate than Whisper large-v3 on German. Parakeet-TDT-0.6B-v3 (CC-BY-4.0, ONNX,
  native word timestamps, German FLEURS 5.04 %) and Granite-Speech-4.1-2B-Plus (Apache-2.0, GGUF, German word-timing
  error ≈ 20–40 ms vs ≈ 130 ms for WhisperX, no language detection) are strong pilots.
- Image-text embeddings: nothing newer beats SigLIP 2 under our licence/runtime constraints (jina, MetaCLIP 2 are
  non-commercial; Qwen3-VL-Embedding has no official ONNX and is VLM-sized).
- Text embeddings: Harrier-oss-v1-270m (Mar 2026, MIT, ONNX, 640 d, MTEB multilingual v2 66.5) is a likely upgrade
  over multilingual-e5-base.
- Audio events: CED (Apache-2.0, ONNX in repo, AudioSet mAP 48–50 at 7–87 MB) clearly beats YAMNet (≈ 0.31).

## Decision
1. **VLM profiles** (data in `worker/profiles.toml`, thinking always disabled via `chat_template_kwargs`):

   | Profile | Default | Baseline / fallback |
   |---|---|---|
   | xl (≥ 16 GB) | Qwen3.5-9B Q8_0 + mmproj F16 | Qwen3.5-9B Q6_K |
   | gpu8 (8 GB) | Qwen3.5-9B Q4_K_M if the M4 benchmark on the reference RTX 3070 shows stable headroom with 3 images, otherwise Qwen3.5-4B Q8_0 | Qwen3-VL-8B Q4_K_M as regression baseline |
   | gpu4 (4–6 GB) | Qwen3.5-4B Q4_K_M | Qwen3.5-2B |
   | cpu | MiniCPM-V 4.6 Q4_K_M or Qwen3.5-2B | vision stage optional |

   The final gpu8 choice is made in M4 by an A/B run on the golden set (shot size, camera angle, people count,
   OCR, German description quality) and recorded in `docs/eval/`.
2. **ASR** stays whisper.cpp large-v3-turbo (ADR-0008). An ASR backend interface allows Parakeet-TDT-v3 (ONNX, CPU)
   and Granite-Speech-4.1-2B-Plus (llama.cpp) as selectable engines; they are piloted in M4. Parakeet requires
   CC-BY attribution in `THIRD_PARTY.md`/NOTICE if shipped as a download.
3. **Image-text embeddings** unchanged: SigLIP 2 base-patch16-256.
4. **Text embeddings**: default stays multilingual-e5-base until the M4 German retrieval benchmark; Harrier-oss-v1-270m
   is evaluated and replaces it if it wins (new 640-d table, re-index).
5. **Audio events**: CED-Mini (default) / CED-Small (GPU or strong CPU) replace YAMNet; the grouping layer from
   ADR-0009 maps the 527 AudioSet classes by display name.
6. Excluded by licence: jina-clip-v2, jina-embeddings-v5, MetaCLIP 2 (CC-BY-NC), EmbeddingGemma (Gemma terms),
   LFM2.5-VL (custom licence).

## Consequences
Smaller, faster defaults with equal or better public benchmark results; decisions on gpu8 VLM and text embeddings are
deferred to measured results on our own data. Model choice stays configuration, not code.

## Sources
- https://huggingface.co/Qwen/Qwen3.5-4B, https://huggingface.co/unsloth/Qwen3.5-9B-GGUF
- https://huggingface.co/google/gemma-4-E4B-it, https://huggingface.co/openbmb/MiniCPM-V-4.6-gguf
- https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3, https://huggingface.co/ibm-granite/granite-speech-4.1-2b-plus
- https://huggingface.co/microsoft/harrier-oss-v1-270m, https://huggingface.co/mispeech/ced-mini
