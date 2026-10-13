"""Generic parser for camera sidecar XML (Sony NonRealTimeMeta style and similar).

The XML is flattened into ``sidecar:`` keys that the mapping table can reference:

- ``sidecar:Device.modelName``: element path (namespaces dropped) plus attribute name; the first
  occurrence of a path wins.
- ``sidecar:item.CaptureGammaEquation``: ``<Item name="..." value="..."/>`` anywhere.
- ``sidecar:table.ShutterSpeed_Time``: first ``<Event value="..."/>`` of ``<ChangeTable name=...>``.
"""

from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree.ElementTree import Element

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

MAX_SIDECAR_BYTES = 2 * 1024 * 1024
SIDECAR_PREFIX = "sidecar:"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def flatten_sidecar(xml_bytes: bytes) -> dict[str, str]:
    """Flatten sidecar XML to ``sidecar:`` keys. Raises ``ValueError`` for invalid or unsafe XML."""
    try:
        root = ElementTree.fromstring(xml_bytes)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        raise ValueError(f"invalid sidecar XML: {exc}") from exc
    flat: dict[str, str] = {}

    def put(key: str, value: str) -> None:
        flat.setdefault(SIDECAR_PREFIX + key, value)

    def walk(element: Element, path: tuple[str, ...]) -> None:
        name = _local(element.tag)
        here = (*path, name)
        dotted = ".".join(here[1:]) if len(here) > 1 else name
        if name == "Item" and "name" in element.attrib and "value" in element.attrib:
            put(f"item.{element.attrib['name']}", element.attrib["value"])
        elif name == "ChangeTable" and "name" in element.attrib:
            for event in element:
                if "value" in event.attrib:
                    put(f"table.{element.attrib['name']}", event.attrib["value"])
                    break
        else:
            for attribute, value in element.attrib.items():
                put(f"{dotted}.{_local(attribute)}" if len(here) > 1 else _local(attribute), value)
        for child in element:
            walk(child, here)

    walk(root, ())
    return flat


def sidecar_candidates(files: list[tuple[str, str]]) -> list[str]:
    """Pick the XML sidecars of an asset from ``(role, rel_path)`` pairs (``sidecar`` role)."""
    return sorted(
        rel_path
        for role, rel_path in files
        if role == "sidecar" and PurePosixPath(rel_path).suffix.lower() == ".xml"
    )


def read_sidecar(path: Path) -> dict[str, Any]:
    """Read and flatten one sidecar file (size-limited)."""
    size = path.stat().st_size
    if size > MAX_SIDECAR_BYTES:
        raise ValueError(f"sidecar {path.name} is larger than {MAX_SIDECAR_BYTES} bytes")
    return dict(flatten_sidecar(path.read_bytes()))
