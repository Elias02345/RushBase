"""Name sanitiser for generated folder names (ADR-0005, decision 6).

Everything Rushbase derives from metadata and puts into a path passes through
:func:`sanitize_component`: Windows-reserved characters and names, trailing dots and spaces,
control characters, Unicode normalisation (NFC) and length limits. Original file names are
never sanitised (they keep their spelling); only generated names are.
"""

import unicodedata
from collections.abc import Collection
from typing import Final

RESERVED_CHARS: Final = frozenset('<>:"/\\|?*')
RESERVED_NAMES: Final = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)
MAX_COMPONENT_CHARS: Final = 120
"""Default limit of a generated path component (characters)."""
MAX_COMPONENT_BYTES: Final = 255
"""Hard limit of any path component in UTF-8 bytes (ext4, NTFS, APFS agree on 255)."""
REPLACEMENT: Final = "_"


def nfc(value: str) -> str:
    """Return ``value`` in Unicode normalisation form C."""
    return unicodedata.normalize("NFC", value)


def collision_key(name: str) -> str:
    """Identity key for case-insensitive comparisons: NFC + casefold (ADR-0005, decision 5)."""
    return nfc(name).casefold()


def _strip_edges(value: str) -> str:
    return value.strip(" ").rstrip(". ")


def _fit_bytes(value: str, max_bytes: int) -> str:
    while len(value.encode("utf-8")) > max_bytes:
        value = value[:-1]
    return value


def _is_reserved(name: str) -> bool:
    return name.split(".", 1)[0].rstrip(" ").upper() in RESERVED_NAMES


def sanitize_component(value: str, max_chars: int = MAX_COMPONENT_CHARS) -> str:
    """Make ``value`` safe as one path component on SMB shares, Windows, macOS and Linux.

    * reserved characters ``< > : " / \\ | ? *`` and control characters become ``_``
    * leading dots become ``_`` (no hidden folders, no ``.rushbase`` impersonation)
    * trailing dots and spaces are removed; reserved device names get a ``_`` prefix
    * the result is NFC, at most ``max_chars`` characters and 255 UTF-8 bytes, never empty
    """
    text = nfc(value)
    text = "".join(
        REPLACEMENT if (ch in RESERVED_CHARS or ord(ch) < 32 or ord(ch) == 0x7F) else ch
        for ch in text
    )
    text = _strip_edges(text)
    stripped = text.lstrip(".")
    if len(stripped) != len(text):
        text = REPLACEMENT * (len(text) - len(stripped)) + stripped
    text = _strip_edges(_fit_bytes(text[:max_chars], MAX_COMPONENT_BYTES))
    if not text:
        return REPLACEMENT
    if _is_reserved(text):
        text = _strip_edges(_fit_bytes(REPLACEMENT + text, MAX_COMPONENT_BYTES))
    return text or REPLACEMENT


def numbered_name(name: str, index: int) -> str:
    """Return ``name`` with ``_<index>`` appended to the stem (``clip.mov`` -> ``clip_2.mov``).

    ``index`` 1 returns the name unchanged (the first candidate carries no suffix).
    """
    if index <= 1:
        return name
    dot = name.rfind(".")
    stem, ext = (name, "") if dot <= 0 else (name[:dot], name[dot:])
    return f"{stem}_{index}{ext}"


def first_free_index(names: Collection[str], taken_keys: Collection[str]) -> int:
    """Smallest ``n`` so that no ``numbered_name(name, n)`` collides with ``taken_keys``.

    ``taken_keys`` holds :func:`collision_key` values of names already present in the target.
    All ``names`` of one asset share the index so that RAW/JPEG/sidecar pairs keep matching stems.
    """
    index = 1
    while any(collision_key(numbered_name(n, index)) in taken_keys for n in names):
        index += 1
    return index
