"""Mapping of free terms to approved taxonomy values (exact, synonym, embedding similarity)."""

import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from rushbase_worker.taxonomy import TaxonomySnapshot

Method = Literal["exact", "synonym", "embedding"]
SYNONYM_CONFIDENCE = 0.95
Embed = Callable[[Sequence[str]], NDArray[np.float32]]


def normalize(term: str) -> str:
    """Lower-case, fold umlauts/accents, replace separators by single spaces."""
    folded = unicodedata.normalize("NFKD", term.casefold())
    stripped = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[\s_\-/]+", " ", stripped).strip()


@dataclass(frozen=True)
class Match:
    """An approved value a term maps to."""

    field: str
    value: str
    confidence: float
    method: Method


@dataclass(frozen=True)
class _Candidate:
    field: str
    value: str
    term: str
    is_value: bool


class TaxonomyMatcher:
    """Maps terms against one taxonomy snapshot; embeddings are computed once per snapshot."""

    def __init__(self, taxonomy: TaxonomySnapshot, embed: Embed, threshold: float) -> None:
        self._threshold = threshold
        self._embed = embed
        self._candidates: list[_Candidate] = []
        self._exact: dict[str, _Candidate] = {}
        for field in taxonomy.fields:
            for value in field.values:
                for term, is_value in [(value.value, True), *((s, False) for s in value.synonyms)]:
                    candidate = _Candidate(field.key, value.value, term, is_value)
                    self._candidates.append(candidate)
                    self._exact.setdefault(f"{field.key}\0{normalize(term)}", candidate)
        self._matrix: NDArray[np.float32] | None = None

    @property
    def empty(self) -> bool:
        return not self._candidates

    def _vectors(self) -> NDArray[np.float32]:
        if self._matrix is None:
            self._matrix = self._embed([c.term for c in self._candidates])
        return self._matrix

    def match(
        self, term: str, *, field: str | None = None, use_embedding: bool = True
    ) -> Match | None:
        """Best approved value for ``term`` (optionally within one field), or ``None``."""
        key = normalize(term)
        if not key or self.empty:
            return None
        for candidate in self._candidates:
            if (field is None or candidate.field == field) and normalize(candidate.term) == key:
                return Match(
                    candidate.field,
                    candidate.value,
                    1.0 if candidate.is_value else SYNONYM_CONFIDENCE,
                    "exact" if candidate.is_value else "synonym",
                )
        if not use_embedding:
            return None
        return self._by_embedding(term, field)

    def _by_embedding(self, term: str, field: str | None) -> Match | None:
        best = self.nearest(term, field)
        if best is None or best[1] < self._threshold:
            return None
        candidate, similarity = best
        return Match(candidate.field, candidate.value, similarity, "embedding")

    def nearest(self, term: str, field: str | None = None) -> tuple[_Candidate, float] | None:
        """The most similar candidate and its cosine similarity."""
        if self.empty:
            return None
        similarities = self._vectors() @ self._embed([term])[0]
        order = np.argsort(-similarities)
        for index in order:
            candidate = self._candidates[int(index)]
            if field is None or candidate.field == field:
                return candidate, float(similarities[int(index)])
        return None
