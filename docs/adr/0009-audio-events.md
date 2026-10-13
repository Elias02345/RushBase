# ADR-0009: Audio event classification

- Status: Accepted
- Date: 2026-10-13

## Context
The `audio` stage tags events (speech, music, gunshot, explosion, applause, wind, silence) on every worker incl.
CPU-only. Candidates: YAMNet (Apache-2.0, ~4 MB, 521 AudioSet classes), AST (BSD-3, int8 ONNX 91 MB, 527 classes,
more accurate), PANNs (no maintained ONNX, weights licence unclear), CLAP (zero-shot, heavy), BEATs (no ONNX).

## Decision
1. Default **YAMNet**, converted once to ONNX in Rushbase CI from the official Apache-2.0 model; ONNX file and
   `yamnet_class_map.csv` published with SHA256. Input 16 kHz mono, 0.96 s windows, 0.48 s hop.
2. Classes are looked up by `display_name`, never by hardcoded index; missing names fail at startup.
3. A grouping layer maps raw classes to Rushbase tags (`gunshot`, `explosion`, `speech`, `music`, `applause`,
   `wind`, `silence`) with per-tag thresholds in config, smoothing (2 hits within 1 s) and segment merging.
   Top-3 raw classes per segment are stored so thresholds can be retuned without reprocessing.
4. Optional "precise" profile: AST int8 (`onnx-community/ast-finetuned-audioset-10-10-0.4593-ONNX`) reusing the
   grouping layer.
5. Loudness (EBU R128) via FFmpeg `ebur128`; music BPM/key for pure audio files via lightweight DSP (M4).

## Consequences
Events are weak signals combined with VLM and transcript context. Known confusions (gunshot vs fireworks)
are surfaced via confidence and the review list.

## Sources
- https://github.com/tensorflow/models/tree/master/research/audioset/yamnet
- https://huggingface.co/onnx-community/ast-finetuned-audioset-10-10-0.4593-ONNX
