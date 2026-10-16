"""ONNX Runtime sessions (CPU by default; CUDA when ``onnxruntime-gpu`` is installed and usable)."""

from pathlib import Path
from typing import Any

import onnxruntime as ort
from tokenizers import Tokenizer


def available_providers() -> list[str]:
    """Execution providers of the installed ONNX Runtime build."""
    return list(ort.get_available_providers())


def choose_providers(use_gpu: bool) -> list[str]:
    """CUDA first when asked for and available, always CPU as the last resort."""
    providers = available_providers()
    chosen = ["CUDAExecutionProvider"] if use_gpu and "CUDAExecutionProvider" in providers else []
    return [*chosen, "CPUExecutionProvider"]


def open_session(path: Path, *, use_gpu: bool, threads: int | None = None) -> ort.InferenceSession:
    """Create an inference session for ``path``."""
    options = ort.SessionOptions()
    options.log_severity_level = 3
    if threads:
        options.intra_op_num_threads = threads
    return ort.InferenceSession(str(path), options, providers=choose_providers(use_gpu))


def active_provider(session: ort.InferenceSession) -> str:
    """The provider a session actually runs on (first one that was accepted)."""
    providers: Any = session.get_providers()
    return str(providers[0])


def load_tokenizer(path: Path) -> Tokenizer:
    """A ``tokenizers`` tokenizer from ``tokenizer.json`` (no ``transformers`` needed)."""
    return Tokenizer.from_file(str(path))
