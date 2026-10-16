"""multilingual-e5-base (ONNX, quantized): ``passage:`` / ``query:`` prefixes, mean pooling."""

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray
from tokenizers import Tokenizer

from rushbase_worker.embedding.siglip import DIM, OnnxSession, l2_normalize

MAX_TOKENS = 512
BATCH = 16


class TextEmbedder:
    """Sentence embeddings for retrieval (768 dimensions, L2-normalised)."""

    def __init__(self, session: OnnxSession, tokenizer: Tokenizer) -> None:
        self._session = session
        self._tokenizer = tokenizer
        self._tokenizer.enable_truncation(max_length=MAX_TOKENS)
        self._tokenizer.enable_padding()

    def embed(self, texts: Sequence[str], *, prefix: str = "passage: ") -> NDArray[np.float32]:
        """Embeddings of ``texts`` (each prefixed with ``prefix``), shape ``(n, 768)``."""
        rows: list[NDArray[np.float32]] = []
        for offset in range(0, len(texts), BATCH):
            encodings = self._tokenizer.encode_batch(
                [prefix + t for t in texts[offset : offset + BATCH]]
            )
            ids = np.array([e.ids for e in encodings], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
            hidden = np.asarray(
                self._session.run(None, {"input_ids": ids, "attention_mask": mask})[0],
                dtype=np.float32,
            )
            weights = mask[:, :, None].astype(np.float32)
            pooled = (hidden * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1.0)
            rows.append(pooled.astype(np.float32))
        return (
            l2_normalize(np.concatenate(rows, axis=0)) if rows else np.zeros((0, DIM), np.float32)
        )
