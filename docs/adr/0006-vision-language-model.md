# ADR-0006: Vision-language model via llama.cpp

- Status: Accepted (amended by ADR-0011)
- Date: 2026-10-13

## Context
The `vision` stage needs a VLM that runs without PyTorch on CPU, NVIDIA, AMD, Intel and Apple hardware.
llama.cpp `llama-server --mmproj` covers all backends. Official GGUF repos `Qwen/Qwen3-VL-{2B,4B,8B,32B}-Instruct-GGUF`
(Apache-2.0). Sizes (8B): Q4_K_M 5.03 GB, Q8_0 8.71 GB, mmproj Q8_0 0.75 GB / F16 1.16 GB. 4B: Q4_K_M 2.50 GB,
mmproj Q8_0 0.45 GB. Newer candidates: Qwen3.5 2B/4B/9B (Apache-2.0, hybrid linear attention → small KV cache,
thinking on by default), Gemma 4 E4B/12B, MiniCPM-V 4.6, InternVL3. `llama-server` supports
`--image-min-tokens/--image-max-tokens` and `response_format: {type: json_schema, schema}` (converted to GBNF).

## Decision
1. Default family **Qwen3-VL-Instruct** (official repos). Qwen3.5-9B/4B is an alternate profile to be A/B-tested
   on the golden set; allowed only with thinking disabled.
2. Hardware profiles are data (`worker/profiles.toml`), selected by detected free VRAM:

   | Profile | Model | mmproj | Flags |
   |---|---|---|---|
   | xl (≥16 GB) | Qwen3-VL-8B Q8_0 | F16 | `-c 16384` |
   | gpu8 (8 GB) | Qwen3-VL-8B Q4_K_M | Q8_0 | `-c 8192 -ctk q8_0 -ctv q8_0 -fa on` |
   | gpu4 (4–6 GB) | Qwen3-VL-4B Q4_K_M | Q8_0 | `-c 8192` |
   | cpu | Qwen3-VL-4B Q4_K_M (2B on weak CPUs) | Q8_0 | `-c 4096`, ≤256 image tokens |

3. Common flags: `--jinja -ngl 99 -np 1 --image-min-tokens 128 --image-max-tokens 512`. Up to 3 keyframes per call.
4. Every call sends a flat JSON schema (enums for closed vocabularies), validates with pydantic, treats
   `finish_reason=length` as failure, retries once at temperature 0.
5. The loader catches out-of-memory at startup and falls back to the next smaller profile; the profile used is
   stored in every result ("analysis quality").
6. On ≤8 GB cards the VLM and Whisper never share the GPU; stages run batch-wise with model unload in between.

## Consequences
Estimated 8 GB footprint ≈ 7.0–7.5 GB (UNVERIFIED, measured in M4 on the reference RTX 3070). Grammar guarantees
shape, not semantic accuracy → golden-set evaluation and confidence thresholds remain mandatory.

## Sources
- https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF
- https://huggingface.co/unsloth/Qwen3.5-9B-GGUF
- https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
