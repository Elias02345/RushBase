"""Frame rates, rational times (FCPXML) and timecodes (EDL)."""

import re
from dataclasses import dataclass
from fractions import Fraction

# Broadcast rates are exact fractions; anything within the tolerance snaps to them.
_NTSC = {23.976: (24000, 1001), 29.97: (30000, 1001), 47.952: (48000, 1001),
         59.94: (60000, 1001), 119.88: (120000, 1001)}  # fmt: skip
_TOLERANCE = 0.01
_TC = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})([:;.])(\d{2,3})$")


@dataclass(frozen=True)
class FrameRate:
    """An exact frame rate ``num/den`` frames per second."""

    num: int
    den: int

    @classmethod
    def from_float(cls, fps: float | None) -> "FrameRate":
        """Snap a measured rate (23.976, 29.97, 25.0 ...) to its exact fraction."""
        if not fps or fps <= 0:
            return cls(25, 1)
        for approx, (num, den) in _NTSC.items():
            if abs(fps - approx) < _TOLERANCE:
                return cls(num, den)
        fraction = Fraction(fps).limit_denominator(1001)
        return cls(fraction.numerator, fraction.denominator)

    @property
    def fps(self) -> float:
        """The rate as a float."""
        return self.num / self.den

    @property
    def nominal(self) -> int:
        """Frames per timecode second (24 for 23.976, 30 for 29.97)."""
        return round(self.fps)

    @property
    def frame_duration(self) -> str:
        """FCPXML ``frameDuration`` (seconds per frame), e.g. ``1001/30000s``."""
        return f"{self.den}/{self.num}s"

    def frames(self, milliseconds: int) -> int:
        """Whole frames in ``milliseconds`` (rounded to the nearest frame)."""
        return round(milliseconds * self.num / (self.den * 1000))

    def rational(self, frames: int) -> str:
        """``frames`` as an FCPXML time value, e.g. ``2002/24000s`` (``0s`` for zero)."""
        if frames == 0:
            return "0s"
        return f"{frames * self.den}/{self.num}s"


def parse_timecode(value: str | None, rate: FrameRate) -> int | None:
    """Frames since midnight for ``HH:MM:SS:FF`` / ``HH:MM:SS;FF`` / ``HH:MM:SS.mmm`` (or ``None``)."""
    match = _TC.match((value or "").strip())
    if match is None:
        return None
    hours, minutes, seconds = (int(match.group(i)) for i in (1, 2, 3))
    last = match.group(5)
    nominal = rate.nominal
    if match.group(4) == "." and len(last) == 3:
        frame = round(int(last) * nominal / 1000)
    else:
        frame = int(last)
    return ((hours * 60 + minutes) * 60 + seconds) * nominal + frame


def format_timecode(frames: int, rate: FrameRate) -> str:
    """Non-drop-frame ``HH:MM:SS:FF`` for a frame count."""
    nominal = rate.nominal
    seconds, frame = divmod(max(frames, 0), nominal)
    minutes, second = divmod(seconds, 60)
    hour, minute = divmod(minutes, 60)
    return f"{hour % 24:02d}:{minute:02d}:{second:02d}:{frame:02d}"
