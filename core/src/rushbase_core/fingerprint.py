"""Quick file fingerprint: size plus BLAKE3 of three 64 KiB samples.

The quick fingerprint identifies likely duplicates immediately, while the full BLAKE3 of a
file is computed later by a worker (``hash`` stage). It reads at most 192 KiB per file, which
keeps scanning large network shares cheap.
"""

from pathlib import Path
from typing import Final

import blake3

SAMPLE_SIZE: Final = 64 * 1024


def quick_fingerprint(path: Path) -> str:
    """Return ``"<size>:<blake3-hex>"`` for ``path``.

    Files up to three samples long are hashed completely; larger files contribute the first,
    middle and last 64 KiB. The size is part of the digest input and of the result.
    """
    size = path.stat().st_size
    hasher = blake3.blake3()
    hasher.update(size.to_bytes(8, "big"))
    with path.open("rb") as handle:
        if size <= 3 * SAMPLE_SIZE:
            hasher.update(handle.read())
        else:
            for offset in (0, (size - SAMPLE_SIZE) // 2, size - SAMPLE_SIZE):
                handle.seek(offset)
                hasher.update(handle.read(SAMPLE_SIZE))
    return f"{size}:{hasher.hexdigest()}"
