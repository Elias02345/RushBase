"""The probe mapping table: normalised field <- candidate tag keys + converter.

Supporting a new vendor means adding candidate keys to a rule (data, not code). Keys:

- ``Group:Tag``: exact ExifTool key (``-G1`` naming), for example ``ExifIFD:ISO``.
- ``Tag``: that tag in any ExifTool group (first occurrence).
- ``ffprobe:video.<key>`` / ``ffprobe:audio.<key>`` / ``ffprobe:format.<key>``: flattened ffprobe
  fields of the first video stream, first audio stream and the container (tags are
  ``...tags.<name>``).
- ``sidecar:...``: see :mod:`rushbase_worker.probe.sidecar`.

For every rule the first candidate whose converter returns a value wins.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from rushbase_worker.probe import convert as c


@dataclass(frozen=True)
class Rule:
    """One normalised field and where it can come from."""

    field: str
    keys: tuple[str, ...]
    convert: c.Converter = c.to_str


class TagIndex:
    """Flat lookup over all tool outputs (exact ``Group:Tag`` and bare ``Tag`` access)."""

    def __init__(self, exiftool: Mapping[str, Any], extra: Mapping[str, Any]) -> None:
        self._exact: dict[str, Any] = {**exiftool, **extra}
        self._bare: dict[str, Any] = {}
        for key, value in exiftool.items():
            tag = key.split(":", 1)[-1]
            self._bare.setdefault(tag, value)

    def get(self, key: str) -> Any:
        """Return the value for ``key`` (see module docstring) or ``None``."""
        if ":" in key:
            return self._exact.get(key)
        return self._bare.get(key)


def resolve(rule: Rule, index: TagIndex) -> Any:
    """Return the first convertible value of ``rule`` or ``None``."""
    for key in rule.keys:
        raw = index.get(key)
        if raw is None:
            continue
        try:
            value = rule.convert(raw)
        except (ValueError, TypeError, ArithmeticError):
            continue
        if value is not None:
            return value
    return None


def apply_rules(rules: tuple[Rule, ...], index: TagIndex) -> dict[str, Any]:
    """Resolve all ``rules`` into a nested dict following the dotted ``field`` paths."""
    result: dict[str, Any] = {}
    for rule in rules:
        *parents, leaf = rule.field.split(".")
        target = result
        for parent in parents:
            target = target.setdefault(parent, {})
        if leaf in target:
            continue
        value = resolve(rule, index)
        if value is not None:
            target[leaf] = value
    return {k: v for k, v in result.items() if v != {}}


_VIDEO = "ffprobe:video."
_AUDIO = "ffprobe:audio."
_FORMAT = "ffprobe:format."

RULES: tuple[Rule, ...] = (
    # --- clip identity -----------------------------------------------------------------------
    Rule("reel", ("XMP-xmpDM:TapeName", "ReelName", "sidecar:item.ReelName", "Reel")),
    Rule("clip_name", ("XMP-xmpDM:ShotName", "ClipName", "sidecar:item.ClipName")),
    Rule("scene", ("XMP-xmpDM:Scene", "Scene", "sidecar:item.SceneName")),
    Rule("take", ("XMP-xmpDM:Take", "TakeNumber", "Take", "sidecar:item.TakeNumber")),
    # --- camera ------------------------------------------------------------------------------
    Rule(
        "camera.make",
        (
            "IFD0:Make",
            "Keys:Make",
            "QuickTime:Make",
            "XMP-tiff:Make",
            "sidecar:Device.manufacturer",
        ),
    ),
    Rule(
        "camera.model",
        (
            "IFD0:Model",
            "Keys:Model",
            "QuickTime:Model",
            "XMP-tiff:Model",
            "sidecar:Device.modelName",
        ),
    ),
    Rule(
        "camera.serial",
        (
            "ExifIFD:BodySerialNumber",
            "MakerNotes:SerialNumber",
            "XMP-aux:SerialNumber",
            "SerialNumber",
            "sidecar:Device.serialNo",
        ),
    ),
    Rule("camera.firmware", ("MakerNotes:FirmwareVersion", "XMP-aux:Firmware", "FirmwareVersion")),
    Rule(
        "camera.recording_format",
        ("sidecar:VideoFormat.VideoFrame.videoCodec", "MakerNotes:RecordingFormat"),
    ),
    Rule("camera.sensor_mode", ("MakerNotes:SensorMode", "SensorMode")),
    # --- lens --------------------------------------------------------------------------------
    Rule(
        "lens.model",
        ("ExifIFD:LensModel", "XMP-aux:Lens", "sidecar:Lens.modelName", "LensModel", "LensType"),
    ),
    Rule("lens.focal_length_mm", ("ExifIFD:FocalLength", "FocalLength"), c.positive_rational),
    Rule(
        "lens.focal_length_35mm",
        ("ExifIFD:FocalLengthIn35mmFormat", "Composite:FocalLength35efl"),
        c.positive_rational,
    ),
    Rule(
        "lens.aperture",
        ("ExifIFD:FNumber", "Composite:Aperture", "sidecar:item.IrisFNumber"),
        c.positive_rational,
    ),
    Rule("lens.t_stop", ("sidecar:item.LensTStop", "TStop"), c.positive_rational),
    Rule(
        "lens.focus_distance_m",
        ("ExifIFD:SubjectDistance", "Composite:FocusDistance2", "MakerNotes:FocusDistance"),
        c.positive_rational,
    ),
    # --- exposure ----------------------------------------------------------------------------
    Rule(
        "exposure.iso",
        ("ExifIFD:ISO", "sidecar:table.ExposureIndexOfPhotoMeter", "sidecar:item.ISOSensitivity"),
        c.iso,
    ),
    Rule(
        "exposure.shutter_s",
        ("ExifIFD:ExposureTime", "sidecar:table.ShutterSpeed_Time", "Composite:ShutterSpeed"),
        c.positive_rational,
    ),
    Rule(
        "exposure.shutter_angle",
        ("sidecar:table.ShutterSpeed_Angle", "ShutterAngle"),
        c.positive_rational,
    ),
    Rule(
        "exposure.white_balance_k",
        ("sidecar:table.WhiteBalance", "MakerNotes:ColorTemperature", "ColorTemperature"),
        c.kelvin,
    ),
    Rule("exposure.tint", ("MakerNotes:WBShiftGM", "Tint"), c.to_float),
    Rule("exposure.nd_filter", ("sidecar:table.NeutralDensityFilterPosition", "NDFilter")),
    Rule("exposure.gamma", ("sidecar:item.CaptureGammaEquation", "MakerNotes:Gamma", "GammaCurve")),
    Rule(
        "exposure.log_profile",
        ("sidecar:item.CaptureGammaEquation", "Gamma", "MakerNotes:PictureProfile"),
        c.log_profile,
    ),
    Rule(
        "exposure.color_space",
        (
            "sidecar:item.CaptureColorPrimaries",
            _VIDEO + "color_primaries",
            "ExifIFD:ColorSpace",
        ),
        c.color_space_name,
    ),
    Rule("exposure.embedded_lut", ("MakerNotes:LUTName", "sidecar:item.LutName")),
    Rule("exposure.hdr", (_VIDEO + "color_transfer",), c.hdr_from_transfer),
    # --- video -------------------------------------------------------------------------------
    Rule("video.codec", (_VIDEO + "codec_name",)),
    Rule("video.profile", (_VIDEO + "profile",)),
    Rule(
        "video.bit_depth",
        (_VIDEO + "bits_per_raw_sample",),
        c.positive_int,
    ),
    Rule("video.bit_depth", (_VIDEO + "pix_fmt",), c.bit_depth_from_pix_fmt),
    Rule("video.chroma_subsampling", (_VIDEO + "pix_fmt",), c.chroma_from_pix_fmt),
    Rule("video.bitrate", (_VIDEO + "bit_rate", _FORMAT + "bit_rate"), c.positive_int),
    Rule("video.width", (_VIDEO + "width",), c.positive_int),
    Rule("video.height", (_VIDEO + "height",), c.positive_int),
    Rule("video.fps", (_VIDEO + "avg_frame_rate", _VIDEO + "r_frame_rate"), c.frame_rate),
    Rule(
        "video.capture_fps",
        (
            "sidecar:VideoFormat.VideoFrame.captureFps",
            "sidecar:item.CaptureFrameRate",
            "Keys:CaptureFrameRate",
        ),
        c.frame_rate,
    ),
    Rule("video.rotation", (_VIDEO + "rotation_cw", "QuickTime:Rotation"), c.sensor_rotation),
    Rule(
        "video.timecode",
        (
            _VIDEO + "tags.timecode",
            _FORMAT + "tags.timecode",
            "sidecar:LtcChangeTable.LtcChange.value",
            "QuickTime:TimeCode",
        ),
        c.timecode,
    ),
    # --- audio -------------------------------------------------------------------------------
    Rule("audio.codec", (_AUDIO + "codec_name",)),
    Rule("audio.channels", (_AUDIO + "channels", "RIFF:NumChannels"), c.positive_int),
    Rule("audio.sample_rate", (_AUDIO + "sample_rate", "RIFF:SampleRate"), c.positive_int),
    Rule(
        "audio.bit_depth",
        (_AUDIO + "bits_per_raw_sample", _AUDIO + "bits_per_sample", "RIFF:BitsPerSample"),
        c.positive_int,
    ),
    Rule("audio.recorder", ("RIFF:Originator", "BWF:Originator"), c.to_str),
    Rule("audio.microphone", ("iXML:Microphone", "XMP-xmpDM:AudioMicrophone")),
    # --- location / attitude -----------------------------------------------------------------
    Rule("location.lat", ("Composite:GPSLatitude", "GPS:GPSLatitude"), c.to_float),
    Rule("location.lat", ("QuickTime:GPSCoordinates", "Keys:GPSCoordinates"), c.split_part(0)),
    Rule(
        "location.lat",
        (_FORMAT + "tags.location", _FORMAT + "tags.com.apple.quicktime.location.ISO6709"),
        c.iso6709_part(0),
    ),
    Rule("location.lon", ("Composite:GPSLongitude", "GPS:GPSLongitude"), c.to_float),
    Rule("location.lon", ("QuickTime:GPSCoordinates", "Keys:GPSCoordinates"), c.split_part(1)),
    Rule(
        "location.lon",
        (_FORMAT + "tags.location", _FORMAT + "tags.com.apple.quicktime.location.ISO6709"),
        c.iso6709_part(1),
    ),
    Rule("location.alt_m", ("Composite:GPSAltitude", "GPS:GPSAltitude"), c.to_float),
    Rule("location.alt_m", ("QuickTime:GPSCoordinates", "Keys:GPSCoordinates"), c.split_part(2)),
    Rule("location.heading_deg", ("GPS:GPSImgDirection", "GPS:GPSTrack"), c.to_float),
    Rule(
        "attitude.pitch_deg",
        ("XMP-drone-dji:GimbalPitchDegree", "XMP-Camera:Pitch", "XMP-drone-dji:FlightPitchDegree"),
        c.to_float,
    ),
    Rule(
        "attitude.roll_deg",
        ("XMP-drone-dji:GimbalRollDegree", "XMP-Camera:Roll", "XMP-drone-dji:FlightRollDegree"),
        c.to_float,
    ),
    Rule(
        "attitude.yaw_deg",
        ("XMP-drone-dji:GimbalYawDegree", "XMP-Camera:Yaw", "XMP-drone-dji:FlightYawDegree"),
        c.to_float,
    ),
    # --- photo -------------------------------------------------------------------------------
    Rule("photo.flash", ("ExifIFD:Flash", "Composite:Flash"), c.flash_text),
    Rule("photo.burst_group", ("MakerNotes:BurstUUID", "BurstUUID")),
    Rule("photo.rating", ("XMP-xmp:Rating", "IFD0:Rating"), c.to_int),
)
"""The table. The first rule for a field that yields a value wins."""
