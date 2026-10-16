"""Camera motion from dense optical flow (pure functions on grayscale numpy frames).

Flow is computed with Farneback's algorithm on a 160 px wide frame. Per frame pair the global
translation (median flow) and the zoom rate (radial divergence) are extracted; a shot's series
is then classified as static / pan / tilt / zoom / handheld / mixed. Velocities are expressed in
pixels per second of the 160 px flow frame so the thresholds do not depend on the frame rate.
"""

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from rushbase_core.results import CameraMotion

Gray = NDArray[np.uint8]
Series = NDArray[np.float64]

FLOW_WIDTH = 160
STATIC_SPEED = 6.0
"""Below this mean speed (px/s) with low jitter a shot is static."""
DIRECTIONAL_SPEED = 15.0
"""Mean pan/tilt speed (px/s) that counts as intentional camera movement."""
ZOOM_RATE = 0.03
"""Zoom rate (relative scale change per second) that counts as a zoom."""
HANDHELD_JITTER = 12.0
"""High-frequency motion (px/s) above which the camera is considered handheld."""
CONSISTENCY = 0.7
"""Minimum fraction of frames moving in the dominant direction for a pan/tilt/zoom."""


@dataclass(frozen=True)
class MotionSample:
    """Global motion between two consecutive frames (px per frame; zoom per frame)."""

    dx: float
    dy: float
    zoom: float


@dataclass(frozen=True)
class MotionResult:
    """Classification of a shot's camera motion."""

    motion: CameraMotion
    confidence: float
    jitter: float
    """High-frequency motion in px/s (basis of the shake score)."""


def flow_frame(gray: Gray) -> Gray:
    """Downscale an analysis frame to the flow resolution (no-op if already small)."""
    height, width = gray.shape
    if width <= FLOW_WIDTH:
        return gray
    scaled = cv2.resize(
        gray, (FLOW_WIDTH, max(8, round(height * FLOW_WIDTH / width))), interpolation=cv2.INTER_AREA
    )
    return np.asarray(scaled, dtype=np.uint8)


def global_motion(previous: Gray, current: Gray) -> MotionSample:
    """Estimate camera translation and zoom between two same-sized flow frames."""
    flow = cv2.calcOpticalFlowFarneback(  # type: ignore[call-overload]
        previous, current, None, 0.5, 3, 15, 3, 5, 1.2, 0
    )
    fx, fy = flow[..., 0], flow[..., 1]
    dx, dy = float(np.median(fx)), float(np.median(fy))
    height, width = fx.shape
    ys, xs = np.mgrid[0:height, 0:width]
    rx, ry = xs - (width - 1) / 2.0, ys - (height - 1) / 2.0
    radius_sq = float((rx**2 + ry**2).sum())
    zoom = float(((rx * (fx - dx)) + (ry * (fy - dy))).sum() / radius_sq) if radius_sq else 0.0
    return MotionSample(dx, dy, zoom)


def _moving_average(values: Series, window: int) -> Series:
    window = max(1, min(window, values.size))
    kernel = np.ones(window) / window
    padded = np.pad(values, (window // 2, window - 1 - window // 2), mode="edge")
    return np.asarray(np.convolve(padded, kernel, mode="valid"), dtype=np.float64)


def jitter(dx: Series, dy: Series, fps: float) -> float:
    """Mean magnitude (px/s) of the motion left after removing the smooth (intentional) part."""
    if dx.size < 3:
        return 0.0
    window = max(3, round(fps / 3))
    residual_x = dx - _moving_average(dx, window)
    residual_y = dy - _moving_average(dy, window)
    return float(np.mean(np.hypot(residual_x, residual_y)) * fps)


def _consistent(series: Series) -> float:
    """Fraction of samples that share the sign of the mean."""
    mean = series.mean()
    if mean == 0:
        return 0.0
    return float(np.mean(np.sign(series) == np.sign(mean)))


def classify_motion(dx: Series, dy: Series, zoom: Series, fps: float) -> MotionResult:
    """Classify a series of per-frame global motion samples (px/frame, zoom/frame)."""
    if dx.size < 3:
        return MotionResult(CameraMotion.STATIC, 0.3, 0.0)
    speed_x, speed_y, zoom_rate = (
        float(dx.mean() * fps),
        float(dy.mean() * fps),
        float(zoom.mean() * fps),
    )
    shake = jitter(dx, dy, fps)
    parts: dict[CameraMotion, float] = {}
    if abs(speed_x) >= DIRECTIONAL_SPEED and _consistent(dx) >= CONSISTENCY:
        parts[CameraMotion.PAN] = abs(speed_x) / DIRECTIONAL_SPEED
    if abs(speed_y) >= DIRECTIONAL_SPEED and _consistent(dy) >= CONSISTENCY:
        parts[CameraMotion.TILT] = abs(speed_y) / DIRECTIONAL_SPEED
    if abs(zoom_rate) >= ZOOM_RATE and _consistent(zoom) >= CONSISTENCY:
        parts[CameraMotion.ZOOM] = abs(zoom_rate) / ZOOM_RATE
    if len(parts) > 1:
        return MotionResult(CameraMotion.MIXED, 0.5, shake)
    if parts:
        (motion, strength), *_ = parts.items()
        return MotionResult(motion, min(1.0, 0.5 + strength / 4.0), shake)
    if shake >= HANDHELD_JITTER:
        return MotionResult(
            CameraMotion.HANDHELD, min(1.0, 0.5 + shake / (4 * HANDHELD_JITTER)), shake
        )
    speed = float(np.hypot(speed_x, speed_y))
    if speed < STATIC_SPEED:
        return MotionResult(CameraMotion.STATIC, min(1.0, 1.0 - speed / (2 * STATIC_SPEED)), shake)
    return MotionResult(CameraMotion.MIXED, 0.4, shake)
