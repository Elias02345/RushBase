"""Time-ordered UUIDv7 identifiers (RFC 9562).

Python 3.12 has no ``uuid.uuid7``; this implementation keeps ids strictly increasing within
one process so that ``ORDER BY id`` reproduces creation order (used as the queue tie-break).
"""

import os
import threading
import time
from uuid import UUID

_lock = threading.Lock()
_last_ms = -1
_last_counter = 0


def uuid7(timestamp_ms: int | None = None) -> UUID:
    """Return a new UUIDv7.

    Layout: 48 bit unix milliseconds, version 7, a 12 bit monotonic counter (``rand_a``),
    variant ``10`` and 62 random bits. ``timestamp_ms`` exists for tests.
    """
    global _last_ms, _last_counter
    with _lock:
        if timestamp_ms is not None:  # explicit timestamps bypass the monotonic state
            ms, counter = timestamp_ms, int.from_bytes(os.urandom(2), "big") & 0xFFF
            return _compose(ms, counter)
        ms = int(time.time() * 1000)
        if ms <= _last_ms:
            ms = _last_ms
            _last_counter += 1
            if _last_counter > 0xFFF:  # counter exhausted inside one millisecond
                ms += 1
                _last_counter = 0
        else:
            _last_counter = int.from_bytes(os.urandom(2), "big") & 0x7FF  # leave headroom
        _last_ms = ms
        counter = _last_counter
    return _compose(ms, counter)


def _compose(ms: int, counter: int) -> UUID:
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= counter << 64
    value |= 0b10 << 62
    value |= rand_b
    return UUID(int=value)


def uuid7_timestamp_ms(value: UUID) -> int:
    """Return the unix-millisecond timestamp embedded in a UUIDv7."""
    return value.int >> 80
