"""Single-pass analysis of a downscaled frame stream: shot cuts, motion and quality per shot."""

import math
from dataclasses import dataclass, field

import cv2
import numpy as np
from numpy.typing import NDArray

from rushbase_core.results import CameraMotion
from rushbase_worker.shots import motion, quality
from rushbase_worker.shots.detect import ShotSegmenter

cv2.setNumThreads(1)  # parallelism comes from concurrent jobs, not from OpenCV
QUALITY_STRIDE = 3
"""Sharpness, exposure and noise are measured on every third frame."""

Frame = NDArray[np.uint8]


@dataclass(frozen=True)
class ShotMetrics:
    """Aggregated measures of one shot (``None`` when too few frames to measure)."""

    start_frame: int
    end_frame: int
    camera_motion: CameraMotion | None
    motion_confidence: float | None
    sharpness: float | None
    shake: float | None
    exposure: float | None
    noise: float | None


@dataclass
class FrameAnalyzer:
    """Consume BGR frames in order; :meth:`finish` returns per-shot metrics."""

    fps: float
    min_shot_seconds: float
    _segmenter: ShotSegmenter = field(init=False)
    _previous: NDArray[np.uint8] | None = field(default=None, init=False)
    _dx: list[float] = field(default_factory=list, init=False)
    _dy: list[float] = field(default_factory=list, init=False)
    _zoom: list[float] = field(default_factory=list, init=False)
    _sharp: list[float] = field(default_factory=list, init=False)
    _expo: list[float] = field(default_factory=list, init=False)
    _noise: list[float] = field(default_factory=list, init=False)
    _count: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._segmenter = ShotSegmenter(self.fps, self.min_shot_seconds)

    @property
    def frames(self) -> int:
        """Number of frames consumed so far."""
        return self._count

    def feed(self, frame: Frame) -> None:
        """Analyse the next frame."""
        index = self._count
        self._segmenter.feed(frame)
        gray = np.asarray(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), dtype=np.uint8)
        small = motion.flow_frame(gray)
        if self._previous is None or self._previous.shape != small.shape:
            sample = motion.MotionSample(math.nan, math.nan, math.nan)
        else:
            sample = motion.global_motion(self._previous, small)
        self._previous = small
        self._dx.append(sample.dx)
        self._dy.append(sample.dy)
        self._zoom.append(sample.zoom)
        if index % QUALITY_STRIDE == 0:
            self._sharp.append(quality.sharpness(gray))
            self._expo.append(quality.exposure(gray))
            self._noise.append(quality.noise(gray))
        else:
            self._sharp.append(math.nan)
            self._expo.append(math.nan)
            self._noise.append(math.nan)
        self._count += 1

    def finish(self) -> list[ShotMetrics]:
        """Segment the stream and aggregate the measures per shot."""
        dx, dy, zoom = (np.array(v, dtype=np.float64) for v in (self._dx, self._dy, self._zoom))
        series = {
            "sharp": np.array(self._sharp, dtype=np.float64),
            "expo": np.array(self._expo, dtype=np.float64),
            "noise": np.array(self._noise, dtype=np.float64),
        }
        shots = []
        for start, end in self._segmenter.finish():
            # the first flow sample of a shot spans the cut, so it is left out
            flow = slice(start + 1, end)
            sx, sy, sz = dx[flow], dy[flow], zoom[flow]
            ok = ~(np.isnan(sx) | np.isnan(sy) | np.isnan(sz))
            result = (
                motion.classify_motion(sx[ok], sy[ok], sz[ok], self.fps) if ok.sum() >= 3 else None
            )
            shots.append(
                ShotMetrics(
                    start_frame=start,
                    end_frame=end,
                    camera_motion=result.motion if result else None,
                    motion_confidence=result.confidence if result else None,
                    shake=quality.shake_score(result.jitter) if result else None,
                    sharpness=_median(series["sharp"][start:end]),
                    exposure=_median(series["expo"][start:end]),
                    noise=_median(series["noise"][start:end]),
                )
            )
        return shots


def _median(values: NDArray[np.float64]) -> float | None:
    valid = values[~np.isnan(values)]
    return float(np.median(valid)) if valid.size else None
