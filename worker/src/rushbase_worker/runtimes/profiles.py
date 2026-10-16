"""Loader of ``profiles.toml`` (hardware profiles and VLM launch recipes)."""

import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from rushbase_worker.hardware.model import Profile

PROFILES_FILE = Path(__file__).resolve().parent.parent / "profiles.toml"

# llama.cpp family -> chat template kwargs that turn thinking off (ADR-0011: always disabled).
NO_THINKING = '{"enable_thinking":false}'


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Common(_Frozen):
    args: tuple[str, ...]
    host: str
    port_min: int
    port_max: int
    health_timeout_seconds: float


class Variant(_Frozen):
    """A llama-server launch recipe for one VLM."""

    name: str = ""
    model: str
    family: str
    ctx: int
    image_max_tokens: int
    kv_quant: str = "q8_0"
    ngl: int = 99
    min_free_mb: int = Field(ge=0)
    fallback: str | None = None

    @property
    def chat_template_kwargs(self) -> str | None:
        """Qwen3.5 thinks by default; it is switched off for every call."""
        return NO_THINKING if self.family == "qwen3.5" else None


class ProfileSpec(_Frozen):
    vlm: str
    asr: str
    audio_events: str
    embed_vision: str


class ProfilesFile(_Frozen):
    common: Common
    profiles: dict[str, ProfileSpec]
    variants: dict[str, Variant]

    def variant(self, name: str) -> Variant:
        try:
            return self.variants[name].model_copy(update={"name": name})
        except KeyError:
            raise KeyError(
                f"unknown VLM variant {name!r}; known: {', '.join(self.variants)}"
            ) from None

    def profile(self, profile: Profile) -> ProfileSpec:
        return self.profiles[profile.value]

    def chain(self, name: str) -> list[Variant]:
        """``name`` followed by its fallbacks, largest first."""
        chain: list[Variant] = []
        current: str | None = name
        while current is not None and all(v.name != current for v in chain):
            variant = self.variant(current)
            chain.append(variant)
            current = variant.fallback
        return chain


@lru_cache(maxsize=1)
def load_profiles(path: Path = PROFILES_FILE) -> ProfilesFile:
    """Parse ``profiles.toml`` (cached)."""
    return ProfilesFile.model_validate(tomllib.loads(path.read_text()))
