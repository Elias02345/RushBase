"""SigLIP zero-shot cross-check of VLM attributes (fixed English prompt sets)."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

TEMPERATURE = 100.0
"""Softmax temperature over the class scores (cosine x 100, SigLIP's logit scale is ~110)."""

SHOT_SIZE_PROMPTS: dict[str, str] = {
    "extreme_wide": "an extreme wide shot, a vast landscape seen from far away",
    "wide": "a wide shot showing the whole scene and its surroundings",
    "full": "a full shot of a person from head to toe",
    "medium": "a medium shot of a person from the waist up",
    "medium_close": "a medium close-up of a person from the chest up",
    "close_up": "a close-up of a face or an object",
    "extreme_close_up": "an extreme close-up of a small detail",
    "detail": "a detail shot of a small object",
}
INTERIOR_PROMPTS: dict[str, str] = {
    "interior": "a photo taken indoors inside a room or building",
    "exterior": "a photo taken outdoors under the open sky",
}
Embed = Callable[[Sequence[str]], NDArray[np.float32]]


@dataclass(frozen=True)
class Disagreement:
    """SigLIP prefers another class than the VLM chose."""

    attribute: str
    vlm: str
    siglip: str
    margin: float


def class_probabilities(
    image: NDArray[np.float32], text: NDArray[np.float32]
) -> NDArray[np.float32]:
    """Softmax over the classes for one L2-normalised image vector."""
    scores = TEMPERATURE * (text @ image)
    scores -= scores.max()
    weights = np.exp(scores)
    probabilities: NDArray[np.float32] = (weights / weights.sum()).astype(np.float32)
    return probabilities


class ZeroShot:
    """Precomputes the text embeddings of the prompt sets and compares them to image vectors."""

    def __init__(self, embed_text: Embed) -> None:
        self._sets: dict[str, tuple[list[str], NDArray[np.float32]]] = {}
        for name, prompts in (
            ("shot_size", SHOT_SIZE_PROMPTS),
            ("interior_exterior", INTERIOR_PROMPTS),
        ):
            labels = list(prompts)
            self._sets[name] = (labels, embed_text([prompts[label] for label in labels]))

    def check(
        self, attribute: str, vlm_value: str, image: NDArray[np.float32], margin: float
    ) -> Disagreement | None:
        """A disagreement when SigLIP's top class beats the VLM's class by more than ``margin``."""
        if attribute not in self._sets:
            return None
        labels, text = self._sets[attribute]
        if vlm_value not in labels:
            return None
        probabilities = class_probabilities(image, text)
        top = int(np.argmax(probabilities))
        chosen = labels.index(vlm_value)
        gap = float(probabilities[top] - probabilities[chosen])
        if top != chosen and gap >= margin:
            return Disagreement(attribute, vlm_value, labels[top], gap)
        return None
