"""Value converters for the probe mapping table.

Every converter takes the raw tag value (number, string, list) and returns the normalised value
or ``None`` when it cannot be interpreted. Converters never raise for bad input.
"""

import math
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

Converter = Callable[[Any], Any]

_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_DATETIME = re.compile(
    r"^(\d{4})[:-](\d{2})[:-](\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?\s*(Z|[+-]\d{2}:?\d{2})?$"
)


def to_str(value: Any) -> str | None:
    """Stripped, non-empty string (lists are joined with ``, ``)."""
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def to_float(value: Any) -> float | None:
    """A finite float from a number or a numeric string (units after the number are ignored)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        number = float(value)
    else:
        match = _NUMBER.match(str(value).strip())
        if match is None:
            return None
        number = float(match.group())
    return number if math.isfinite(number) else None


def to_int(value: Any) -> int | None:
    """An integer from a number or numeric string (rounded)."""
    number = to_float(value)
    return None if number is None else round(number)


def positive_int(value: Any) -> int | None:
    """Like :func:`to_int` but ``None`` for zero and negative numbers."""
    number = to_int(value)
    return number if number is not None and number > 0 else None


def positive_float(value: Any) -> float | None:
    """Like :func:`to_float` but ``None`` for zero and negative numbers."""
    number = to_float(value)
    return number if number is not None and number > 0 else None


def rational(value: Any) -> float | None:
    """A number or ratio: ``1/50``, ``1 250`` (numerator denominator) or a plain number."""
    if isinstance(value, str):
        text = value.strip()
        parts = re.split(r"\s*/\s*|\s+", text)
        if len(parts) == 2:
            numerator, denominator = to_float(parts[0]), to_float(parts[1])
            if numerator is None or denominator in (None, 0):
                return None
            assert denominator is not None
            return numerator / denominator
    return to_float(value)


def positive_rational(value: Any) -> float | None:
    """:func:`rational` restricted to values above zero."""
    number = rational(value)
    return number if number is not None and number > 0 else None


def frame_rate(value: Any) -> float | None:
    """Frame rates such as ``25p``, ``29.97i``, ``30000/1001`` or ``25``; ``0/0`` is ``None``."""
    if isinstance(value, str):
        text = value.strip().lower().rstrip("pi")
        return positive_rational(text.replace(" ", "")) if "/" in text else positive_float(text)
    return positive_float(value)


def kelvin(value: Any) -> int | None:
    """A plausible colour temperature in Kelvin (1000-20000)."""
    number = to_int(value)
    return number if number is not None and 1000 <= number <= 20000 else None


def iso(value: Any) -> int | None:
    """A plausible ISO sensitivity."""
    number = to_int(value)
    return number if number is not None and 1 <= number <= 10_000_000 else None


def parse_datetime(value: Any) -> datetime | None:
    """Parse ExifTool (``2026:10:13 09:12:00+02:00``) and ISO 8601 timestamps.

    All-zero dates (unset QuickTime fields) are rejected. Without an offset the result is naive
    (a floating local time).
    """
    text = to_str(value)
    if text is None:
        return None
    match = _DATETIME.match(text)
    if match is None:
        return None
    year, month, day, hour, minute, second, fraction, offset = match.groups()
    if int(year) < 1900:
        return None
    micro = int((fraction or "0")[:6].ljust(6, "0"))
    tz = None
    if offset:
        tz = UTC if offset == "Z" else _offset(offset)
    try:
        return datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second), micro, tz
        )
    except ValueError:
        return None


def _offset(text: str) -> timezone:
    sign = -1 if text.startswith("-") else 1
    digits = text[1:].replace(":", "")
    return timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:4])))


def format_offset(moment: datetime) -> str | None:
    """``UTC`` or ``+HH:MM`` for an aware datetime, else ``None``."""
    offset = moment.utcoffset()
    if offset is None:
        return None
    if offset == timedelta(0):
        return "UTC"
    total = int(offset.total_seconds() // 60)
    sign = "-" if total < 0 else "+"
    return f"{sign}{abs(total) // 60:02d}:{abs(total) % 60:02d}"


def parse_utc_offset(value: Any) -> timezone | None:
    """Parse ``+02:00`` style offsets (ExifTool ``OffsetTimeOriginal``)."""
    text = to_str(value)
    if text is None or not re.fullmatch(r"[+-]\d{2}:?\d{2}", text):
        return None
    return _offset(text)


def timecode(value: Any) -> str | None:
    """Normalise timecodes: ``09120000`` becomes ``09:12:00:00``; ``HH:MM:SS:FF`` is kept."""
    text = to_str(value)
    if text is None:
        return None
    if re.fullmatch(r"\d{8}", text):
        return ":".join(text[i : i + 2] for i in range(0, 8, 2))
    if re.fullmatch(r"\d{2}[:;]\d{2}[:;]\d{2}[:;.]\d{2,3}", text):
        return text
    return None


def log_profile(value: Any) -> str | None:
    """Compact lower-case log profile id (``s-log3-cine`` becomes ``slog3``) or ``None``.

    Only gamma names that denote a log encoding yield a profile; ``bt709`` and similar do not.
    """
    text = to_str(value)
    if text is None:
        return None
    compact = re.sub(r"[^a-z0-9]", "", text.lower())
    if "log" in compact:
        return re.sub(r"(cine|live|ext)$", "", compact) or compact
    return None


def hdr_from_transfer(value: Any) -> str | None:
    """``pq`` or ``hlg`` from an ffprobe ``color_transfer`` name; SDR transfers give ``None``."""
    text = to_str(value)
    return {"smpte2084": "pq", "arib-std-b67": "hlg"}.get(text.lower()) if text else None


def color_space_name(value: Any) -> str | None:
    """Colour space/primaries as a lower-case token; ``unknown``/``reserved`` become ``None``."""
    text = to_str(value)
    if text is None:
        return None
    if text.isdigit():  # EXIF ColorSpace
        return {"1": "srgb", "2": "adobergb"}.get(text)
    lowered = text.lower()
    return None if lowered in {"unknown", "unspecified", "reserved"} else lowered


def flash_text(value: Any) -> str | None:
    """``fired`` / ``did_not_fire`` from the EXIF Flash bit field."""
    number = to_int(value)
    if number is None:
        return None
    return "fired" if number & 1 else "did_not_fire"


def chroma_from_pix_fmt(value: Any) -> str | None:
    """Chroma subsampling (``4:2:0``) from an ffmpeg pixel format name."""
    text = to_str(value)
    if text is None:
        return None
    for token, name in (("444", "4:4:4"), ("422", "4:2:2"), ("420", "4:2:0"), ("411", "4:1:1")):
        if token in text:
            return name
    return None


def bit_depth_from_pix_fmt(value: Any) -> int | None:
    """Bit depth from an ffmpeg pixel format name (``yuv422p10le`` gives 10; ``yuv420p`` 8)."""
    text = to_str(value)
    if text is None:
        return None
    match = re.search(r"p(\d{2})(?:le|be)?$", text)
    if match:
        return int(match.group(1))
    if re.search(r"(yuv|yuvj|gray|rgb|bgr|nv|gbrp)", text):
        return 8
    return None


def split_part(index: int) -> Converter:
    """Converter for space/comma separated tuples (QuickTime ``GPSCoordinates``): part ``index``."""

    def convert(value: Any) -> float | None:
        parts = re.split(r"[,\s]+", str(value).strip())
        return to_float(parts[index]) if index < len(parts) else None

    return convert


def iso6709_part(index: int) -> Converter:
    """Converter for ISO 6709 strings such as ``+37.77-122.41+010.0/``: lat, lon, alt."""

    def convert(value: Any) -> float | None:
        numbers = re.findall(r"[-+]\d+(?:\.\d+)?", str(value))
        return to_float(numbers[index]) if index < len(numbers) else None

    return convert


def sensor_rotation(value: Any) -> int | None:
    """Clockwise display rotation in degrees (0/90/180/270)."""
    number = to_int(value)
    return None if number is None else number % 360
