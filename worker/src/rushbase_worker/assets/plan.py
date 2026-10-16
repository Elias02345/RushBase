"""Which models a hardware profile needs."""

from rushbase_worker.hardware.model import Profile
from rushbase_worker.runtimes.profiles import load_profiles

TEXT_EMBEDDING_MODEL = "multilingual-e5-base"
IMAGE_EMBEDDING_MODEL = "siglip2-base-patch16-256"


def models_for_profile(profile: Profile, extra_variants: tuple[str, ...] = ()) -> list[str]:
    """Model ids of the profile's default VLM variant plus ASR, audio events and embeddings."""
    profiles = load_profiles()
    spec = profiles.profile(profile)
    vlm_ids = [profiles.variant(spec.vlm).model]
    vlm_ids += [profiles.variant(name).model for name in extra_variants]
    ids = [*vlm_ids, spec.asr, spec.audio_events, IMAGE_EMBEDDING_MODEL, TEXT_EMBEDDING_MODEL]
    return list(dict.fromkeys(ids))
