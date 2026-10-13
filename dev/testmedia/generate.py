#!/usr/bin/env python3
"""Generate deterministic synthetic test media into ``<library>/Input``.

Requires ``ffmpeg`` on PATH plus Pillow, NumPy and tifffile (all in the dev dependency group).
Existing files are skipped, so re-running is idempotent and cheap.

Usage: ``make testmedia`` or ``uv run python dev/testmedia/generate.py [--library PATH]``
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

from rushbase_core import init_library

DEFAULT_LIBRARY = Path(__file__).resolve().parents[1] / "library"
BATCH_A = "2026-10-12_Testdreh_Ort-A"
BATCH_B = "2026-10-13_Testdreh_Ort-B"
PARTIAL_SUFFIX = ".generating"

# Flags that keep ffmpeg output reproducible for identical inputs.
FFMPEG_BASE = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-fflags", "+bitexact"]
X264 = [
    "-c:v",
    "libx264",
    "-preset",
    "veryfast",
    "-crf",
    "30",
    "-pix_fmt",
    "yuv420p",
    "-threads",
    "1",
]

Builder = Callable[[Path], None]


def run_ffmpeg(*args: str) -> None:
    """Run ffmpeg with reproducible defaults; raises on failure."""
    subprocess.run([*FFMPEG_BASE, *args], check=True)


def ensure(path: Path, build: Builder, created: list[Path]) -> None:
    """Create ``path`` via ``build`` unless it exists (written atomically via a partial file)."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + PARTIAL_SUFFIX)
    partial.unlink(missing_ok=True)
    build(partial)
    partial.replace(path)
    created.append(path)


def _scene_clip_args(size: str, rate: int, segment: float, frequency: int) -> list[str]:
    """Inputs + filter graph: testsrc -> smptebars -> mandelbrot with a sine tone."""
    duration = str(segment)
    return [
        *["-f", "lavfi", "-t", duration, "-i", f"testsrc=size={size}:rate={rate}"],
        *["-f", "lavfi", "-t", duration, "-i", f"smptebars=size={size}:rate={rate}"],
        *["-f", "lavfi", "-t", duration, "-i", f"mandelbrot=size={size}:rate={rate}"],
        *[
            "-f",
            "lavfi",
            "-t",
            str(segment * 3),
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000",
        ],
        *["-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]"],
        *["-map", "[v]", "-map", "3:a"],
    ]


def build_scene_clip(frequency: int) -> Builder:
    def build(target: Path) -> None:
        run_ffmpeg(
            *_scene_clip_args("1920x1080", 25, 2.0, frequency),
            *X264,
            *["-c:a", "aac", "-b:a", "96k", "-f", "mp4", str(target)],
        )

    return build


def build_uhd_hevc(target: Path) -> None:
    run_ffmpeg(
        *["-f", "lavfi", "-t", "2", "-i", "testsrc2=size=3840x2160:rate=25"],
        *["-f", "lavfi", "-t", "2", "-i", "sine=frequency=330:sample_rate=48000"],
        *["-c:v", "libx265", "-preset", "ultrafast", "-crf", "34", "-pix_fmt", "yuv420p"],
        *["-x265-params", "pools=1:frame-threads=1:log-level=error", "-tag:v", "hvc1"],
        *["-c:a", "aac", "-b:a", "96k", "-f", "mp4", str(target)],
    )


def build_prores(target: Path) -> None:
    run_ffmpeg(
        *["-f", "lavfi", "-t", "2", "-i", "smptebars=size=1920x1080:rate=25"],
        *["-f", "lavfi", "-t", "2", "-i", "sine=frequency=523:sample_rate=48000"],
        *["-c:v", "prores_ks", "-profile:v", "2", "-pix_fmt", "yuv422p10le"],
        *["-c:a", "pcm_s16le", "-f", "mov", str(target)],
    )


def build_portrait(target: Path) -> None:
    """Landscape pixels plus a 90 degree display-rotation tag (as phones record portrait)."""
    landscape = target.with_name(target.name + ".landscape")
    try:
        run_ffmpeg(
            *["-f", "lavfi", "-t", "2", "-i", "testsrc2=size=1920x1080:rate=30"],
            *["-f", "lavfi", "-t", "2", "-i", "sine=frequency=262:sample_rate=48000"],
            *X264,
            *["-c:a", "aac", "-b:a", "96k", "-f", "mp4", str(landscape)],
        )
        run_ffmpeg(
            *["-display_rotation", "90", "-i", str(landscape)],
            *["-c", "copy", "-f", "mp4", str(target)],
        )
    finally:
        landscape.unlink(missing_ok=True)


def build_wav(target: Path) -> None:
    """24-bit WAV with a Broadcast Wave ``bext`` chunk."""
    run_ffmpeg(
        *["-f", "lavfi", "-t", "3", "-i", "sine=frequency=440:sample_rate=48000"],
        *["-ac", "2", "-c:a", "pcm_s24le", "-write_bext", "1"],
        *["-metadata", "description=Synthetic field recording"],
        *["-metadata", "originator=rushbase-testmedia"],
        *["-metadata", "origination_date=2026-10-12", "-metadata", "origination_time=10:00:00"],
        *["-metadata", "time_reference=172800000"],
        *["-f", "wav", str(target)],
    )


def build_mp3(target: Path) -> None:
    run_ffmpeg(
        *["-f", "lavfi", "-t", "3", "-i", "sine=frequency=220:sample_rate=44100"],
        *["-c:a", "libmp3lame", "-b:a", "128k", "-metadata", "title=Synthetic ambience"],
        *["-f", "mp3", str(target)],
    )


def build_flac(target: Path) -> None:
    run_ffmpeg(
        *["-f", "lavfi", "-t", "3", "-i", "sine=frequency=880:sample_rate=48000"],
        *["-c:a", "flac", "-metadata", "title=Synthetic tone", "-f", "flac", str(target)],
    )


def _photo_pixels(seed: int, size: tuple[int, int] = (640, 480)) -> Image.Image:
    rng = np.random.default_rng(seed)
    width, height = size
    gradient = np.linspace(0, 255, width, dtype=np.uint8)[None, :, None].repeat(height, 0)
    noise = rng.integers(0, 40, size=(height, width, 3), dtype=np.uint8)
    pixels = np.clip(gradient.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(pixels, "RGB")


def _exif(taken: str) -> Image.Exif:
    exif = Image.Exif()
    exif[0x010F] = "Example Camera Co"  # Make
    exif[0x0110] = "EX-CAM1"  # Model
    exif[0x0131] = "rushbase-testmedia"  # Software
    exif[0x0132] = taken  # DateTime
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = taken  # DateTimeOriginal
    sub[0x829A] = (1, 250)  # ExposureTime
    sub[0x829D] = (28, 10)  # FNumber
    sub[0x8827] = 400  # ISOSpeedRatings
    sub[0x920A] = (35, 1)  # FocalLength
    sub[0xA434] = "EX 35mm F2.8"  # LensModel
    return exif


def build_jpeg(seed: int, taken: str) -> Builder:
    def build(target: Path) -> None:
        _photo_pixels(seed).save(target, format="JPEG", quality=85, exif=_exif(taken))

    return build


def build_png(seed: int, taken: str) -> Builder:
    def build(target: Path) -> None:
        _photo_pixels(seed).save(target, format="PNG", exif=_exif(taken))

    return build


def build_dng(target: Path) -> None:
    """Write a tiny Bayer (RGGB) DNG-style TIFF so RAW+JPEG pairing can be exercised.

    It carries the DNG version tags and a CFA pattern but no colour calibration, so it is a
    structural stand-in for pairing/probing tests and not a development-grade raw image.
    """
    rng = np.random.default_rng(7)
    mosaic = rng.integers(512, 60000, size=(128, 128), dtype=np.uint16)
    tifffile.imwrite(
        target,
        mosaic,
        photometric="cfa",
        compression=None,
        extratags=[
            (50706, "B", 4, (1, 4, 0, 0), True),  # DNGVersion
            (50707, "B", 4, (1, 1, 0, 0), True),  # DNGBackwardVersion
            (50708, "s", 0, "Example Camera Co EX-CAM1", True),  # UniqueCameraModel
            (33421, "H", 2, (2, 2), True),  # CFARepeatPatternDim
            (33422, "B", 4, (0, 1, 1, 2), True),  # CFAPattern: R G G B
            (50717, "I", 1, 65535, True),  # WhiteLevel
        ],
    )


SONY_LIKE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<NonRealTimeMeta
    xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20"
    lastUpdate="2026-10-13T09:15:02+02:00">
  <TargetMaterial umidRef="060A2B340101010501010D4313000000F1A2B3C4D5E6F70811223344556677CC"/>
  <Duration value="50"/>
  <LtcChangeTable tcFps="25" halfStep="false">
    <LtcChange frameCount="0" value="09120000" status="increment"/>
    <LtcChange frameCount="49" value="09120124" status="end"/>
  </LtcChangeTable>
  <CreationDate value="2026-10-13T09:12:00+02:00"/>
  <VideoFormat>
    <VideoRecPort port="DIRECT"/>
    <VideoFrame videoCodec="AVC_3840_2160_HP@L51" captureFps="25p" formatFps="25p"/>
    <VideoLayout pixel="3840" numOfVerticalLine="2160" aspectRatio="16:9"/>
  </VideoFormat>
  <AudioFormat numOfChannel="2">
    <AudioRecPort port="DIRECT" audioCodec="LPCM16" trackDst="CH1"/>
    <AudioRecPort port="DIRECT" audioCodec="LPCM16" trackDst="CH2"/>
  </AudioFormat>
  <Device manufacturer="Example Camera Co" modelName="EX-CAM1" serialNo="00000001"/>
  <Lens modelName="EX 24-70mm F2.8"/>
  <AcquisitionRecord>
    <Group name="CameraUnitMetadataSet">
      <Item name="CaptureGammaEquation" value="s-log3-cine"/>
      <Item name="CaptureColorPrimaries" value="s-gamut3-cine"/>
    </Group>
    <ChangeTable name="ExposureIndexOfPhotoMeter">
      <Event frameCount="0" value="800"/>
    </ChangeTable>
    <ChangeTable name="ShutterSpeed_Time">
      <Event frameCount="0" value="1/50"/>
    </ChangeTable>
  </AcquisitionRecord>
</NonRealTimeMeta>
"""


def build_card_clip(target: Path) -> None:
    run_ffmpeg(
        *["-f", "lavfi", "-t", "2", "-i", "testsrc2=size=1920x1080:rate=25"],
        *["-f", "lavfi", "-t", "2", "-i", "sine=frequency=392:sample_rate=48000"],
        *X264,
        *["-c:a", "aac", "-b:a", "96k", "-f", "mp4", str(target)],
    )


def write_card_xml(target: Path) -> None:
    target.write_text(SONY_LIKE_XML, encoding="utf-8")


def write_text(content: str) -> Builder:
    def build(target: Path) -> None:
        target.write_text(content, encoding="utf-8")

    return build


def copy_of(source: Path) -> Builder:
    def build(target: Path) -> None:
        shutil.copyfile(source, target)

    return build


def generate(library: Path) -> list[Path]:
    """Generate all test media below ``library/Input`` and return the newly created files."""
    init_library(library)
    created: list[Path] = []
    batch_a = library / "Input" / BATCH_A
    batch_b = library / "Input" / BATCH_B

    clips = [batch_a / f"A001_clip_{n:02d}.mp4" for n in (1, 2, 3)]
    for clip, frequency in zip(clips, (440, 554, 659), strict=True):
        ensure(clip, build_scene_clip(frequency), created)
    ensure(batch_a / "A001_uhd_hevc.mp4", build_uhd_hevc, created)
    ensure(batch_a / "A001_prores.mov", build_prores, created)
    ensure(batch_a / "A001_portrait.mp4", build_portrait, created)
    ensure(batch_a / "audio" / "field_recording.wav", build_wav, created)
    ensure(batch_a / "audio" / "ambience.mp3", build_mp3, created)
    ensure(batch_a / "audio" / "tone.flac", build_flac, created)
    ensure(batch_a / "photos" / "IMG_0001.jpg", build_jpeg(1, "2026:10:12 10:05:00"), created)
    ensure(batch_a / "photos" / "IMG_0001.png", build_png(2, "2026:10:12 10:06:00"), created)
    ensure(batch_a / "photos" / "IMG_0002.dng", build_dng, created)
    ensure(batch_a / "photos" / "IMG_0002.jpg", build_jpeg(3, "2026:10:12 10:07:00"), created)
    ensure(batch_a / "A001_clip_01_copy.mp4", copy_of(clips[0]), created)

    # Junk that the scanner must ignore.
    ensure(batch_a / "._A001_clip_01.mp4", write_text("AppleDouble stub"), created)
    ensure(batch_a / "Thumbs.db", write_text("thumbnail cache"), created)
    ensure(batch_a / "A001_clip_02.mp4.part", write_text("incomplete copy"), created)

    # Fake camera card structure: PRIVATE/M4ROOT/CLIP/C0001.MP4 + C0001M01.XML.
    clip_dir = batch_b / "PRIVATE" / "M4ROOT" / "CLIP"
    ensure(clip_dir / "C0001.MP4", build_card_clip, created)
    ensure(clip_dir / "C0001M01.XML", write_card_xml, created)
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY, help="library root")
    args = parser.parse_args(argv)
    if shutil.which("ffmpeg") is None:
        print("ffmpeg not found on PATH", file=sys.stderr)
        return 1
    created = generate(args.library)
    for path in created:
        print(f"created {path.relative_to(args.library)}")
    print(f"{len(created)} file(s) created" if created else "nothing to do (all files exist)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
