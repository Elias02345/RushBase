"""Map validated stage outputs onto asset columns and JSON documents.

One place defines how a :class:`~rushbase_core.results.ProbeOutput` becomes the technical
columns of ``asset`` and the ``asset_metadata`` documents, so result ingestion and the rebuild
from sidecars agree.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rushbase_core.results import ProbeOutput


def parse_timezone(name: str | None) -> timezone | ZoneInfo | None:
    """Parse an IANA zone name or a ``+HH:MM`` offset; ``None`` when not understood."""
    if not name:
        return None
    text = name.strip()
    if text[:1] in "+-" and len(text) >= 3:
        sign = -1 if text[0] == "-" else 1
        digits = text[1:].replace(":", "")
        if digits.isdigit() and len(digits) in (2, 4):
            hours, minutes = int(digits[:2]), int(digits[2:] or 0)
            return timezone(sign * timedelta(hours=hours, minutes=minutes))
        return None
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def aware_capture(capture_at: datetime | None, capture_tz: str | None) -> datetime | None:
    """Return ``capture_at`` as an aware datetime (naive values are read in ``capture_tz``/UTC)."""
    if capture_at is None or capture_at.tzinfo is not None:
        return capture_at
    return capture_at.replace(tzinfo=parse_timezone(capture_tz) or UTC)


def local_capture_date(capture_at: datetime | None, capture_tz: str | None) -> date | None:
    """Calendar date of the recording where it was shot (falls back to the UTC date)."""
    if capture_at is None:
        return None
    zone = parse_timezone(capture_tz)
    moment = aware_capture(capture_at, capture_tz)
    assert moment is not None  # capture_at is not None
    return moment.astimezone(zone or UTC).date()


def _decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def probe_columns(probe: ProbeOutput) -> dict[str, Any]:
    """Column values of ``asset`` derived from a probe result (``None`` where unknown)."""
    video = probe.video
    location = probe.location
    return {
        "capture_at": aware_capture(probe.capture_at, probe.capture_tz),
        "capture_tz": probe.capture_tz,
        "duration_ms": probe.duration_ms,
        "width": video.width if video else None,
        "height": video.height if video else None,
        "fps": _decimal(video.fps) if video else None,
        "codec": video.codec if video else (probe.audio.codec if probe.audio else None),
        "camera_make": probe.camera.make,
        "camera_model": probe.camera.model,
        "lens_model": probe.lens.model,
        "focal_length_mm": _decimal(probe.lens.focal_length_mm),
        "focal_length_35mm": _decimal(probe.lens.focal_length_35mm),
        "iso": probe.exposure.iso,
        "log_profile": probe.exposure.log_profile,
        "orientation": probe.orientation.value if probe.orientation else None,
        "gps_lat": location.lat if location else None,
        "gps_lon": location.lon if location else None,
        "gps_alt": location.alt_m if location else None,
        "decode_status": probe.decode_status.value,
    }


def probe_documents(probe: ProbeOutput) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(normalized, raw)`` JSON documents of ``asset_metadata`` (raw tool output split off)."""
    normalized = probe.model_dump(mode="json", exclude={"raw", "schema_"})
    return normalized, dict(probe.raw)


def probe_from_documents(normalized: dict[str, Any], raw: dict[str, Any]) -> ProbeOutput:
    """Rebuild a :class:`ProbeOutput` from the stored documents (used by sidecars and summaries)."""
    return ProbeOutput.model_validate({**normalized, "raw": raw})
