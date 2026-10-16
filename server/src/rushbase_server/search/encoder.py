"""Query encoders: the text towers of SigLIP 2 and multilingual-e5 on ONNX Runtime (CPU).

ADR-0012: the only model inference on the server. Workers index with the same models; vectors are
only comparable when model id (and revision, if configured) match, see :class:`EncoderInfo`.

Layout and pins come from the worker's lock file (``worker/models.lock.json``) and its model
store, ``<models_dir>/<model-id>/<file name>`` (``RUSHBASE_MODELS_DIR`` points at the worker's
``<data_dir>/models``). SigLIP uses the ``text-fp16`` tower (int8 failed the fidelity bar,
ADR-0011); e5 uses ``model`` + ``tokenizer``. Revisions mirror the worker's ``ModelRef``: for e5 the
first 12 hex characters of the model file's SHA-256. Image vectors are matched by model id only,
because the worker's revision there depends on the vision precision it chose (fp16 or int8).
"""

import hashlib
import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from rushbase_server.settings import Settings

logger = logging.getLogger(__name__)

DIM = 768
QUERY_CACHE_SIZE = 512
SIGLIP_TEXT_LENGTH = 64
LOCK_PATH = Path(__file__).resolve().parents[4] / "worker" / "models.lock.json"
SIGLIP_FILES = ("text_model_fp16.onnx", "tokenizer.json")
E5_FILES = ("model_quantized.onnx", "tokenizer.json")


def _from_lock(
    lock: dict[str, Any], model_id: str, roles: tuple[str, str]
) -> tuple[tuple[str, str], str | None] | None:
    """File names for ``roles`` and the SHA-256 prefix of the first role's file, if pinned."""
    entry = lock.get("models", {}).get(model_id)
    if entry is None:
        return None
    by_role = {f["role"]: f for f in entry["files"]}
    if not all(r in by_role for r in roles):
        return None
    names = tuple(by_role[r]["filename"].rsplit("/", 1)[-1] for r in roles)
    return (names[0], names[1]), str(by_role[roles[0]]["sha256"])[:12]


def load_layout(settings: Settings) -> dict[str, tuple[str, str | None, tuple[str, str]]]:
    """``{space: (model_id, revision, (model file, tokenizer file))}`` from the worker lock."""
    lock: dict[str, Any] = {}
    if LOCK_PATH.is_file():
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    image = _from_lock(lock, settings.siglip_model_id, ("text-fp16", "tokenizer"))
    text = _from_lock(lock, settings.e5_model_id, ("model", "tokenizer"))
    return {
        "image": (
            settings.siglip_model_id,
            settings.siglip_revision,
            image[0] if image else SIGLIP_FILES,
        ),
        "text": (
            settings.e5_model_id,
            settings.e5_revision or (text[1] if text else None),
            text[0] if text else E5_FILES,
        ),
    }


@dataclass(frozen=True)
class EncoderInfo:
    """Identity of one embedding space the server can encode queries for."""

    space: str
    """``image`` (SigLIP text tower, cross-modal) or ``text`` (e5)."""
    model_id: str
    revision: str | None
    available: bool
    reason: str | None = None
    """Why the encoder is unavailable (shown by ``/search/capabilities``)."""
    warming: bool = False
    """The model is being loaded in the background; the first query would otherwise pay for it."""


class QueryEncoder(Protocol):
    """Encodes search queries into the two vector spaces."""

    def info(self, space: str) -> EncoderInfo:
        """Identity and availability of ``space`` (``image`` or ``text``)."""
        ...

    def encode_image_query(self, text: str) -> list[float]:
        """SigLIP text-tower vector for text-to-image retrieval."""
        ...

    def encode_text_query(self, text: str) -> list[float]:
        """e5 vector (``query: `` prefix) for text-to-text retrieval."""
        ...

    def warm(self) -> None:
        """Load the models and run one query through them (blocking; call from a thread)."""
        ...


class NullEncoder:
    """No models configured: search degrades to full-text."""

    def __init__(self, reason: str = "RUSHBASE_MODELS_DIR is not configured") -> None:
        self._reason = reason

    def info(self, space: str) -> EncoderInfo:
        return EncoderInfo(space, "", None, False, self._reason)

    def encode_image_query(self, text: str) -> list[float]:
        raise RuntimeError(self._reason)

    def encode_text_query(self, text: str) -> list[float]:
        raise RuntimeError(self._reason)

    def warm(self) -> None:
        """Nothing to load."""


class FakeEncoder:
    """Deterministic hash-based vectors for tests: equal words give similar vectors.

    A query vector is the normalised sum of per-word pseudo-random vectors, so texts that share
    words are closer than unrelated ones (:func:`fake_vector` builds the same space for indexing).
    """

    def __init__(
        self,
        image_model: tuple[str, str | None] = ("siglip-fake", "r1"),
        text_model: tuple[str, str | None] = ("e5-fake", "r1"),
    ) -> None:
        self._models = {"image": image_model, "text": text_model}

    def info(self, space: str) -> EncoderInfo:
        model_id, revision = self._models[space]
        return EncoderInfo(space, model_id, revision, True)

    def encode_image_query(self, text: str) -> list[float]:
        return fake_vector(text, "image")

    def encode_text_query(self, text: str) -> list[float]:
        return fake_vector(text, "text")

    def warm(self) -> None:
        """Nothing to load."""


def _word_vector(word: str, salt: str) -> list[float]:
    values: list[float] = []
    counter = 0
    while len(values) < DIM:
        digest = hashlib.sha256(f"{salt}:{word}:{counter}".encode()).digest()
        values += [(b - 127.5) / 128 for b in digest]
        counter += 1
    return values[:DIM]


def fake_vector(text: str, salt: str = "") -> list[float]:
    """L2-normalised bag-of-words hash vector (the space of :class:`FakeEncoder`)."""
    total = [0.0] * DIM
    for word in text.lower().split() or [""]:
        for i, v in enumerate(_word_vector(word, salt)):
            total[i] += v
    norm = sum(v * v for v in total) ** 0.5 or 1.0
    return [v / norm for v in total]


class OnnxEncoder:
    """Lazy-loading ONNX Runtime encoder for both text towers."""

    def __init__(self, settings: Settings) -> None:
        assert settings.models_dir is not None
        self._dir = settings.models_dir
        self._threads = settings.encoder_threads
        self._ids = load_layout(settings)
        self._sessions: dict[str, tuple[Any, Any]] = {}
        self._lock = threading.Lock()
        self._warming = False
        self._cache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()

    def _missing(self, space: str) -> list[str]:
        model_id, _revision, files = self._ids[space]
        return [f for f in files if not (self._dir / model_id / f).is_file()]

    def info(self, space: str) -> EncoderInfo:
        model_id, revision, _files = self._ids[space]
        missing = self._missing(space)
        reason = (
            f"missing files in {self._dir / model_id}: {', '.join(missing)}" if missing else None
        )
        warming = self._warming and space not in self._sessions and not missing
        return EncoderInfo(space, model_id, revision, not missing, reason, warming)

    def warm(self) -> None:
        """Load both towers and encode one query each, so the first real search is fast."""
        self._warming = True
        try:
            for space in ("image", "text"):
                if self._missing(space):
                    continue
                encode = self.encode_image_query if space == "image" else self.encode_text_query
                encode("warm-up")
        finally:
            self._warming = False

    def _cached(self, space: str, text: str, compute: Callable[[str], list[float]]) -> list[float]:
        """Small LRU of query vectors: repeated queries (needs items, paging) skip the model."""
        key = (space, text)
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return list(hit)
        vector = compute(text)
        with self._lock:
            self._cache[key] = vector
            while len(self._cache) > QUERY_CACHE_SIZE:
                self._cache.popitem(last=False)
        return list(vector)

    def _load(self, space: str) -> tuple[Any, Any]:
        with self._lock:
            if space not in self._sessions:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                model_id, _revision, (model_file, tokenizer_file) = self._ids[space]
                options = ort.SessionOptions()
                options.intra_op_num_threads = self._threads
                session = ort.InferenceSession(
                    str(self._dir / model_id / model_file),
                    options,
                    providers=["CPUExecutionProvider"],
                )
                tokenizer = Tokenizer.from_file(str(self._dir / model_id / tokenizer_file))
                self._sessions[space] = (session, tokenizer)
            return self._sessions[space]

    def encode_image_query(self, text: str) -> list[float]:
        return self._cached("image", text, self._encode_image)

    def encode_text_query(self, text: str) -> list[float]:
        return self._cached("text", text, self._encode_text)

    def _encode_image(self, text: str) -> list[float]:
        import numpy as np

        session, tokenizer = self._load("image")
        pad = tokenizer.token_to_id("<pad>") or 0
        ids = np.full((1, SIGLIP_TEXT_LENGTH), pad, dtype=np.int64)
        tokens = tokenizer.encode(text.lower()).ids[:SIGLIP_TEXT_LENGTH]
        ids[0, : len(tokens)] = tokens
        output = np.asarray(session.run(["pooler_output"], {"input_ids": ids})[0], dtype=np.float32)
        return _normalise(output[0])

    def _encode_text(self, text: str) -> list[float]:
        import numpy as np

        session, tokenizer = self._load("text")
        tokenizer.enable_truncation(max_length=512)
        tokenizer.enable_padding()
        encoding = tokenizer.encode("query: " + text)
        ids = np.array([encoding.ids], dtype=np.int64)
        mask = np.array([encoding.attention_mask], dtype=np.int64)
        hidden = np.asarray(
            session.run(None, {"input_ids": ids, "attention_mask": mask})[0], dtype=np.float32
        )
        weights = mask[:, :, None].astype(np.float32)
        pooled = (hidden * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1.0)
        return _normalise(pooled[0])


def _normalise(vector: Any) -> list[float]:
    import numpy as np

    norm = float(np.linalg.norm(vector)) or 1.0
    return [float(x) for x in (vector / norm)]


def build_encoder(settings: Settings) -> QueryEncoder:
    """The configured encoder; :class:`NullEncoder` when no models directory is set."""
    if settings.models_dir is None:
        return NullEncoder()
    if not Path(settings.models_dir).is_dir():
        return NullEncoder(f"models directory {settings.models_dir} does not exist")
    return OnnxEncoder(settings)
