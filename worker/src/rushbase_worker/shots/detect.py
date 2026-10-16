"""Shot boundaries with PySceneDetect's ``AdaptiveDetector`` on already-decoded frames."""

import math
from itertools import pairwise

import numpy as np
from numpy.typing import NDArray
from scenedetect import AdaptiveDetector

DETECTOR_NAME = "pyscenedetect-adaptive-0.6.6"
Frame = NDArray[np.uint8]


class ShotSegmenter:
    """Feed BGR analysis frames one by one; get cut positions (frame indices) at the end."""

    def __init__(self, fps: float, min_shot_seconds: float) -> None:
        self._min_len = max(1, math.ceil(fps * min_shot_seconds))
        self._detector = AdaptiveDetector(min_scene_len=self._min_len)
        self._cuts: set[int] = set()
        self._frames = 0

    def feed(self, frame: Frame) -> None:
        """Process the next frame (indices are assigned in order, starting at 0)."""
        self._cuts.update(self._detector.process_frame(self._frames, frame))
        self._frames += 1

    def finish(self) -> list[tuple[int, int]]:
        """Return ``(first_frame, end_frame_exclusive)`` per shot, covering all frames."""
        if self._frames == 0:
            return []
        self._cuts.update(self._detector.post_process(self._frames - 1))
        cuts = sorted(c for c in self._cuts if 0 < c < self._frames)
        return merge_short(cuts, self._frames, self._min_len)


def merge_short(cuts: list[int], total_frames: int, min_len: int) -> list[tuple[int, int]]:
    """Turn cut frames into shot ranges, dropping cuts that would leave a shot below ``min_len``.

    A too-short final shot is merged into its predecessor.
    """
    accepted: list[int] = []
    last = 0
    for cut in cuts:
        if cut - last >= min_len:
            accepted.append(cut)
            last = cut
    if accepted and total_frames - accepted[-1] < min_len:
        accepted.pop()
    bounds = [0, *accepted, total_frames]
    return list(pairwise(bounds))
