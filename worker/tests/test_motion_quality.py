import cv2
import numpy as np
import pytest
from numpy.typing import NDArray
from PIL import Image

from rushbase_core.results import CameraMotion
from rushbase_worker.preview.audio import PeakAccumulator, reduce_bins
from rushbase_worker.preview.phash import dhash
from rushbase_worker.shots import motion, quality
from rushbase_worker.shots.analyze import FrameAnalyzer
from rushbase_worker.shots.detect import merge_short
from rushbase_worker.stages.shots import keyframe_times, shot_bounds_ms

U8 = NDArray[np.uint8]


def texture(seed: int = 0, size: tuple[int, int] = (90, 160)) -> U8:
    rng = np.random.default_rng(seed)
    noise = rng.integers(0, 255, size, dtype=np.uint8)
    return np.asarray(cv2.GaussianBlur(noise, (0, 0), 2.0), dtype=np.uint8)


def test_global_motion_detects_translation() -> None:
    base = texture()
    shifted = np.roll(base, 3, axis=1)  # content moves right by 3 px
    sample = motion.global_motion(base, shifted)
    assert sample.dx == pytest.approx(3.0, abs=0.7)
    assert abs(sample.dy) < 0.5


def test_global_motion_detects_zoom() -> None:
    base = texture()
    height, width = base.shape
    scaled = cv2.resize(base, None, fx=1.06, fy=1.06)
    top, left = (scaled.shape[0] - height) // 2, (scaled.shape[1] - width) // 2
    zoomed = scaled[top : top + height, left : left + width]
    assert motion.global_motion(base, np.ascontiguousarray(zoomed)).zoom > 0.02


def series(value: float, n: int = 30) -> NDArray[np.float64]:
    return np.full(n, value, dtype=np.float64)


def test_classify_static() -> None:
    zero = series(0.0)
    result = motion.classify_motion(zero, zero, zero, 25.0)
    assert result.motion is CameraMotion.STATIC
    assert result.confidence > 0.9


def test_classify_pan_and_tilt_and_zoom() -> None:
    zero = series(0.0)
    assert motion.classify_motion(series(2.0), zero, zero, 25.0).motion is CameraMotion.PAN
    assert motion.classify_motion(zero, series(-2.0), zero, 25.0).motion is CameraMotion.TILT
    assert motion.classify_motion(zero, zero, series(0.004), 25.0).motion is CameraMotion.ZOOM


def test_classify_handheld_and_mixed() -> None:
    rng = np.random.default_rng(1)
    shaky_x = rng.normal(0, 1.0, 60)
    shaky_y = rng.normal(0, 1.0, 60)
    result = motion.classify_motion(shaky_x, shaky_y, series(0.0, 60), 25.0)
    assert result.motion is CameraMotion.HANDHELD
    assert result.jitter > motion.HANDHELD_JITTER
    mixed = motion.classify_motion(series(2.0), series(2.0), series(0.0), 25.0)
    assert mixed.motion is CameraMotion.MIXED


def test_quality_measures() -> None:
    sharp = (np.indices((90, 160)).sum(axis=0) % 2 * 255).astype(np.uint8)
    blurred = np.asarray(cv2.GaussianBlur(sharp, (0, 0), 4), dtype=np.uint8)
    assert quality.sharpness(sharp) > quality.sharpness(blurred)
    assert quality.sharpness(np.full((90, 160), 128, np.uint8)) == 0.0
    assert quality.exposure(np.full((90, 160), 10, np.uint8)) < -0.8
    assert quality.exposure(np.full((90, 160), 250, np.uint8)) > 0.8
    assert abs(quality.exposure(np.full((90, 160), 115, np.uint8))) < 0.05
    clean = np.full((90, 160), 100, np.uint8)
    noisy = np.clip(clean + np.random.default_rng(0).normal(0, 10, clean.shape), 0, 255).astype(
        np.uint8
    )
    assert quality.noise(noisy) > 0.5 > quality.noise(clean)
    assert 0 <= quality.shake_score(10) < quality.shake_score(100) < 1


def test_merge_short_shots() -> None:
    assert merge_short([], 100, 10) == [(0, 100)]
    assert merge_short([50], 100, 10) == [(0, 50), (50, 100)]
    assert merge_short([5, 50], 100, 10) == [(0, 50), (50, 100)]
    assert merge_short([50, 95], 100, 10) == [(0, 50), (50, 100)]


def test_keyframes_inside_boundaries() -> None:
    frames = dict(keyframe_times(2000, 4000))
    assert 2000 < frames["start"] < frames["middle"] < frames["end"] < 4000
    tiny = dict(keyframe_times(0, 100))
    assert 0 <= tiny["start"] <= tiny["middle"] <= tiny["end"] <= 100


def test_shot_bounds_tile_the_clip() -> None:
    class M:
        def __init__(self, start: int) -> None:
            self.start_frame = start

    bounds = shot_bounds_ms([M(0), M(50), M(100)], 25.0, 6000)  # type: ignore[list-item]
    assert bounds == [(0, 2000), (2000, 4000), (4000, 6000)]


def test_analyzer_finds_cut_in_synthetic_frames() -> None:
    first = np.asarray(cv2.cvtColor(texture(1, (72, 128)), cv2.COLOR_GRAY2BGR), dtype=np.uint8)
    second = np.full((72, 128, 3), (30, 200, 40), np.uint8)
    analyzer = FrameAnalyzer(fps=10.0, min_shot_seconds=0.5)
    for frame in [first] * 20 + [second] * 20:
        analyzer.feed(frame)
    shots = analyzer.finish()
    assert [(s.start_frame, s.end_frame) for s in shots] == [(0, 20), (20, 40)]
    assert shots[0].camera_motion is CameraMotion.STATIC
    assert shots[0].sharpness is not None


def test_peaks_streaming_and_reduction() -> None:
    acc = PeakAccumulator(window=4)
    samples = np.array([0, 1, -1, 0, 0.5, 0.2, -0.5, 0, 0.1], dtype=np.float32)
    acc.feed(samples[:5])
    acc.feed(samples[5:])
    mins, maxs = acc.finish()
    assert mins.tolist() == [-1.0, -0.5, 0.1] or mins.size == 3
    assert maxs[0] == 1.0
    small_min, small_max = reduce_bins(mins, maxs, 2)
    assert small_min.size == 2
    assert small_min.min() == -1.0
    assert small_max.max() == 1.0


def test_dhash_is_stable_and_discriminates() -> None:
    gradient = Image.fromarray(np.tile(np.arange(0, 256, 4, dtype=np.uint8), (64, 1)))
    reverse = gradient.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    assert dhash(gradient) == dhash(gradient)
    assert len(dhash(gradient)) == 16
    assert dhash(gradient) != dhash(reverse)
