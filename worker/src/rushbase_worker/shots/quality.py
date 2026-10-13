"""Per-frame image quality measures (pure functions on 8-bit grayscale numpy frames).

All measures run on the reduced analysis frame (about 320 px wide), so their scales are
relative to that resolution: comparable between shots of one library, not absolute lab values.
"""

import math

import cv2
import numpy as np
from numpy.typing import NDArray

Gray = NDArray[np.uint8]

SHARPNESS_SCALE = 200.0
"""Laplacian variance at which the sharpness score reaches 1 - 1/e (about 0.63)."""
MID_GRAY = 0.45
"""Median luma (0..1) that counts as a balanced exposure."""
NOISE_SCALE = 12.0
"""Estimated noise sigma (8-bit levels) that maps to a noise score of 1."""
SHAKE_SCALE = 25.0
"""High-frequency motion (px/s on the 160 px flow frame) at which the shake score is 0.5."""


def sharpness(gray: Gray) -> float:
    """Normalised Laplacian variance, 0 (flat or blurred) to 1 (very sharp)."""
    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return float(1.0 - math.exp(-variance / SHARPNESS_SCALE))


def exposure(gray: Gray) -> float:
    """Median luma relative to mid gray: -1 heavily underexposed, 0 balanced, 1 overexposed."""
    median = float(np.median(gray)) / 255.0
    return float(np.clip((median - MID_GRAY) / MID_GRAY, -1.0, 1.0))


_NOISE_KERNEL = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float64)


def noise(gray: Gray) -> float:
    """Noise estimate in 0..1 (Immerkaer's fast sigma estimator, scaled by ``NOISE_SCALE``)."""
    height, width = gray.shape
    if height < 3 or width < 3:
        return 0.0
    response = cv2.filter2D(gray.astype(np.float64), -1, _NOISE_KERNEL)[1:-1, 1:-1]
    sigma = math.sqrt(math.pi / 2.0) / 6.0 * float(np.abs(response).mean())
    return float(np.clip(sigma / NOISE_SCALE, 0.0, 1.0))


def shake_score(jitter_px_per_s: float) -> float:
    """Map high-frequency camera motion to 0..1 (0 = steady)."""
    jitter = max(0.0, jitter_px_per_s)
    return jitter / (jitter + SHAKE_SCALE)
