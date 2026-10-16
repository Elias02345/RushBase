"""The taxonomy snapshot (``GET /api/v1/worker/taxonomy``) the AI stages work against."""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)

CACHE_SECONDS = 300.0


class _Wire(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class TaxonomyValue(_Wire):
    value: str
    synonyms: list[str] = Field(default_factory=list)
    description: str | None = None


class TaxonomyField(_Wire):
    key: str
    label_de: str
    label_en: str
    kind: Literal["single", "multi"]
    values: list[TaxonomyValue]


class TaxonomyFlag(_Wire):
    key: str
    description_de: str
    description_en: str


class TaxonomySnapshot(_Wire):
    """Approved vocabulary and review flags; ``version 0`` = the server has none (yet)."""

    version: int = 0
    fields: list[TaxonomyField] = Field(default_factory=list)
    flags: list[TaxonomyFlag] = Field(default_factory=list)

    def field(self, key: str) -> TaxonomyField | None:
        return next((f for f in self.fields if f.key == key), None)


EMPTY_SNAPSHOT = TaxonomySnapshot()

Fetcher = Callable[[], Awaitable[TaxonomySnapshot]]


class TaxonomySource:
    """Caches the snapshot for a few minutes; without a fetcher (local runs) it stays empty."""

    def __init__(
        self, fetch: Fetcher | None = None, *, fixed: TaxonomySnapshot | None = None
    ) -> None:
        self._fetch = fetch
        self._fixed = fixed
        self._cached: TaxonomySnapshot | None = None
        self._at = 0.0

    async def get(self) -> TaxonomySnapshot:
        """The current snapshot (empty when unavailable)."""
        if self._fixed is not None:
            return self._fixed
        if self._fetch is None:
            return EMPTY_SNAPSHOT
        if self._cached is None or time.monotonic() - self._at > CACHE_SECONDS:
            self._cached = await self._fetch()
            self._at = time.monotonic()
        return self._cached
