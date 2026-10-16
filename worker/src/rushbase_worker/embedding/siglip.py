"""SigLIP 2 (base, patch16-256) towers on ONNX Runtime: preprocessing, tokenization, vectors."""

import base64
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageOps
from tokenizers import Tokenizer

from rushbase_core.results_ai import Vector

IMAGE_SIZE = 256
TEXT_LENGTH = 64
DIM = 768
IMAGE_BATCH = 16


class OnnxSession(Protocol):
    """The part of an ONNX Runtime session the embedders use."""

    def run(self, output_names: list[str] | None, input_feed: dict[str, object]) -> list[object]:
        """Run the model."""
        ...


def preprocess(image: Image.Image) -> NDArray[np.float32]:
    """RGB -> 256x256 bilinear (squashing, as the HF processor), x/255, mean 0.5 / std 0.5, CHW."""
    rgb = ImageOps.exif_transpose(image).convert("RGB")
    resized = rgb.resize((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.BILINEAR)
    array = np.asarray(resized, dtype=np.float32) / 255.0
    return np.ascontiguousarray(((array - 0.5) / 0.5).transpose(2, 0, 1))


def l2_normalize(vectors: NDArray[np.float32]) -> NDArray[np.float32]:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return (vectors / np.maximum(norms, 1e-12)).astype(np.float32)


def to_vector(row: NDArray[np.float32]) -> Vector:
    """Contract vector (base64 little-endian float32) of an L2-normalised row."""
    packed = np.ascontiguousarray(row, dtype="<f4").tobytes()
    return Vector(dim=len(row), b64=base64.b64encode(packed).decode("ascii"))


def from_vector(vector: Vector) -> NDArray[np.float32]:
    """Decode a contract vector."""
    return np.frombuffer(base64.b64decode(vector.b64), dtype="<f4").astype(np.float32)


class ImageEmbedder:
    """The SigLIP 2 vision tower."""

    def __init__(self, session: OnnxSession) -> None:
        self._session = session

    def embed(self, images: Sequence[Image.Image]) -> NDArray[np.float32]:
        """L2-normalised image embeddings, shape ``(n, 768)``."""
        rows: list[NDArray[np.float32]] = []
        for offset in range(0, len(images), IMAGE_BATCH):
            batch = np.stack([preprocess(i) for i in images[offset : offset + IMAGE_BATCH]])
            outputs = self._session.run(["pooler_output"], {"pixel_values": batch})
            rows.append(np.asarray(outputs[0], dtype=np.float32))
        return (
            l2_normalize(np.concatenate(rows, axis=0)) if rows else np.zeros((0, DIM), np.float32)
        )

    def embed_files(self, paths: Sequence[Path]) -> NDArray[np.float32]:
        """Embed image files."""
        images = []
        for path in paths:
            with Image.open(path) as handle:
                images.append(handle.copy())
        return self.embed(images)


class TextEmbedder:
    """The SigLIP 2 text tower (lower-cased text, padded/truncated to 64 tokens)."""

    def __init__(self, session: OnnxSession, tokenizer: Tokenizer) -> None:
        self._session = session
        self._tokenizer = tokenizer
        self._pad = tokenizer.token_to_id("<pad>") or 0

    def encode(self, texts: Sequence[str]) -> NDArray[np.int64]:
        """Token ids ``(n, 64)`` exactly as the reference processor builds them."""
        ids = np.full((len(texts), TEXT_LENGTH), self._pad, dtype=np.int64)
        for row, text in enumerate(texts):
            tokens = self._tokenizer.encode(text.lower()).ids[:TEXT_LENGTH]
            ids[row, : len(tokens)] = tokens
        return ids

    def embed(self, texts: Sequence[str]) -> NDArray[np.float32]:
        """L2-normalised text embeddings, shape ``(n, 768)``."""
        if not texts:
            return np.zeros((0, DIM), np.float32)
        outputs = self._session.run(["pooler_output"], {"input_ids": self.encode(texts)})
        return l2_normalize(np.asarray(outputs[0], dtype=np.float32))
