"""Perceptual hash: dHash, 64 bits, as 16 lower-case hex digits."""

from PIL import Image


def dhash(image: Image.Image) -> str:
    """Difference hash: 9x8 grayscale, one bit per left-to-right brightness step."""
    small = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(small.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (pixels[row * 9 + col] > pixels[row * 9 + col + 1])
    return f"{bits:016x}"
