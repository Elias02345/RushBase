"""Filing rule templates: parsing, validation and rendering of target directories.

A template is a ``/``-separated path that starts with ``Content``, for example::

    Content/{media_type}/{capture_date:%Y}/{capture_date:%Y-%m-%d}_{batch_label|Unsorted}/

Placeholders are ``{field}``, ``{field|fallback}``, ``{date_field:%fmt}`` and
``{date_field:%fmt|fallback}``. Values are sanitised (:mod:`rushbase_core.sanitize`); a missing
value uses the placeholder's fallback, then the rule's per-field fallback, then a built-in
default. Rendering is pure, so the server (filing, dry-run) and the CLI share it.
"""

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Final

from rushbase_core.layout import CONTENT_DIR
from rushbase_core.sanitize import MAX_COMPONENT_CHARS, collision_key, sanitize_component

DEFAULT_TEMPLATE: Final = (
    "Content/{media_type}/{capture_date:%Y}/{capture_date:%Y-%m-%d}_{batch_label|Unsorted}/"
)
"""Template of the rule that is seeded on a fresh installation."""
MAX_DEPTH: Final = 5
"""Maximum number of directory levels below ``Content/``."""
DEFAULT_PATH_BUDGET: Final = 180
"""Default budget for the library-relative path of the longest file (keeps roots < 240 chars)."""
MIN_SHRUNK_COMPONENT: Final = 8

STATIC_FIELDS: Final = frozenset(
    {
        "media_type",
        "capture_date",
        "batch_label",
        "batch_hint_date",
        "batch_hint_location",
        "batch_hint_project",
        "camera_model",
    }
)
DATE_FIELDS: Final = frozenset({"capture_date", "batch_hint_date"})
TAXONOMY_PREFIX: Final = "field."
DEFAULT_DATE_FORMAT: Final = "%Y-%m-%d"
_ALLOWED_DIRECTIVES: Final = frozenset("YymdBbAajUWGVuwe%")
_TAXONOMY_KEY: Final = re.compile(r"^[A-Za-z0-9_-]+$")
_BUILTIN_FALLBACKS: Final = {"capture_date": "undated", "batch_hint_date": "undated"}
_BUILTIN_DEFAULT: Final = "unknown"


class TemplateError(ValueError):
    """The template is syntactically or semantically invalid."""


@dataclass(frozen=True)
class Placeholder:
    """One ``{field:fmt|fallback}`` expression."""

    name: str
    fmt: str | None = None
    fallback: str | None = None


Part = str | Placeholder


@dataclass(frozen=True)
class Template:
    """A parsed template: ``components`` are the path segments below the library root."""

    components: tuple[tuple[Part, ...], ...]

    @property
    def depth(self) -> int:
        """Directory levels below ``Content/``."""
        return len(self.components) - 1

    def fields(self) -> frozenset[str]:
        """All field names used by the template."""
        return frozenset(
            part.name for comp in self.components for part in comp if isinstance(part, Placeholder)
        )


def _parse_placeholder(body: str) -> Placeholder:
    cut = min((i for i in (body.find(":"), body.find("|")) if i >= 0), default=-1)
    name = body if cut < 0 else body[:cut]
    fmt: str | None = None
    fallback: str | None = None
    if cut >= 0 and body[cut] == ":":
        rest = body[cut + 1 :]
        bar = rest.find("|")
        fmt, fallback = (rest, None) if bar < 0 else (rest[:bar], rest[bar + 1 :])
    elif cut >= 0:
        fallback = body[cut + 1 :]
    name = name.strip()
    _validate_field(name)
    if fmt is not None:
        if name not in DATE_FIELDS:
            raise TemplateError(f"field {name!r} does not take a format")
        _validate_format(fmt)
    if fallback is not None and not fallback.strip():
        raise TemplateError(f"empty fallback in {{{body}}}")
    return Placeholder(name, fmt, fallback)


def _validate_field(name: str) -> None:
    if name in STATIC_FIELDS:
        return
    if name.startswith(TAXONOMY_PREFIX) and _TAXONOMY_KEY.match(name[len(TAXONOMY_PREFIX) :]):
        return
    raise TemplateError(f"unknown field {name!r}")


def _validate_format(fmt: str) -> None:
    if not fmt:
        raise TemplateError("empty date format")
    index = 0
    while index < len(fmt):
        if fmt[index] == "%":
            if index + 1 >= len(fmt) or fmt[index + 1] not in _ALLOWED_DIRECTIVES:
                raise TemplateError(f"unsupported date directive in {fmt!r}")
            index += 2
        else:
            index += 1
    if "/" in date(2000, 1, 2).strftime(fmt):
        raise TemplateError("a date format must not produce '/'")


def parse_template(template: str) -> Template:
    """Parse and validate ``template``.

    Raises:
        TemplateError: syntax error, unknown field/directive, empty or too many levels, or a
            path that does not start with ``Content``.
    """
    components: list[list[Part]] = [[]]
    index = 0
    while index < len(template):
        char = template[index]
        if char == "{":
            end = template.find("}", index)
            if end < 0 or "{" in template[index + 1 : end]:
                raise TemplateError(f"unbalanced '{{' at position {index}")
            components[-1].append(_parse_placeholder(template[index + 1 : end]))
            index = end + 1
        elif char == "}":
            raise TemplateError(f"unbalanced '}}' at position {index}")
        elif char == "/":
            components.append([])
            index += 1
        else:
            end = index
            while end < len(template) and template[end] not in "{}/":
                end += 1
            components[-1].append(template[index:end])
            index = end
    if not components[-1]:
        components.pop()  # a trailing slash is allowed
    if any(not comp for comp in components):
        raise TemplateError("empty path segment")
    parsed = Template(tuple(tuple(comp) for comp in components))
    first = parsed.components[0]
    if len(first) != 1 or first[0] != CONTENT_DIR:
        raise TemplateError(f"the template must start with '{CONTENT_DIR}/'")
    if parsed.depth < 1:
        raise TemplateError("the template needs at least one directory below Content")
    if parsed.depth > MAX_DEPTH:
        raise TemplateError(f"too deep: {parsed.depth} levels (maximum {MAX_DEPTH})")
    return parsed


@dataclass(frozen=True)
class FilingContext:
    """Values a template can use. ``None``/empty values trigger the fallbacks."""

    media_type: str
    capture_date: date | None = None
    batch_label: str | None = None
    batch_hint_date: date | None = None
    batch_hint_location: str | None = None
    batch_hint_project: str | None = None
    camera_model: str | None = None
    taxonomy: Mapping[str, str] = field(default_factory=dict)
    """Values of ``field.<key>`` placeholders (empty until the taxonomy arrives in M4)."""

    def value(self, name: str) -> str | date | None:
        """Raw value of a field (``None`` when unknown)."""
        if name.startswith(TAXONOMY_PREFIX):
            return self.taxonomy.get(name[len(TAXONOMY_PREFIX) :])
        value: str | date | None = getattr(self, name)
        return value


@dataclass(frozen=True)
class Rendered:
    """Result of rendering a template for one asset."""

    components: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def path(self) -> str:
        """The directory as a library-relative ``/``-separated path."""
        return "/".join(self.components)


def _render_placeholder(
    part: Placeholder, ctx: FilingContext, fallbacks: Mapping[str, str], warnings: list[str]
) -> str:
    value = ctx.value(part.name)
    if isinstance(value, date):
        return value.strftime(part.fmt or DEFAULT_DATE_FORMAT)
    if value is not None and value.strip():
        return value
    if part.fallback is not None:
        return part.fallback
    fallback = fallbacks.get(part.name) or _BUILTIN_FALLBACKS.get(part.name, _BUILTIN_DEFAULT)
    warnings.append(f"{part.name} is missing; used '{fallback}'")
    return fallback


def render_directory(
    template: Template,
    ctx: FilingContext,
    fallbacks: Mapping[str, str] | None = None,
    max_component: int = MAX_COMPONENT_CHARS,
) -> Rendered:
    """Render ``template`` for ``ctx`` into sanitised path components (``Content`` first)."""
    warnings: list[str] = []
    result: list[str] = [CONTENT_DIR]
    for comp in template.components[1:]:
        text = "".join(
            part
            if isinstance(part, str)
            else _render_placeholder(part, ctx, fallbacks or {}, warnings)
            for part in comp
        )
        clean = sanitize_component(text, max_component)
        if clean != text:
            warnings.append(f"sanitised {text!r} to {clean!r}")
        result.append(clean)
    return Rendered(tuple(result), tuple(warnings))


def fit_path_budget(
    components: Iterable[str], tail_length: int, budget: int = DEFAULT_PATH_BUDGET
) -> tuple[tuple[str, ...], bool]:
    """Shorten the longest components until ``dir + "/" + tail`` fits ``budget`` characters.

    ``tail_length`` is the length of the longest path below the directory (a file name, or a
    card structure's deepest relative path). The first component (``Content``) is kept.
    Returns ``(components, fits)``; ``fits`` is false when even the minimum length is too long.
    """
    parts = list(components)
    while True:
        total = sum(len(p) for p in parts) + len(parts) + tail_length
        excess = total - budget
        if excess <= 0:
            return tuple(parts), True
        longest = max(range(1, len(parts)), key=lambda i: len(parts[i]), default=None)
        if longest is None or len(parts[longest]) <= MIN_SHRUNK_COMPONENT:
            return tuple(parts), False
        target = max(MIN_SHRUNK_COMPONENT, len(parts[longest]) - excess)
        parts[longest] = sanitize_component(parts[longest][:target], target)


def reconcile_case(
    components: Iterable[str], list_dir: Callable[[tuple[str, ...]], Iterable[str]]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Adopt the spelling of existing directories that differ only in case or normalisation.

    ``list_dir`` returns the names inside a directory (given as components). Returns the
    reconciled components and notes about every adopted spelling. This keeps ``Content/Video``
    and ``Content/video`` from coexisting on case-insensitive shares.
    """
    resolved: list[str] = []
    notes: list[str] = []
    for component in components:
        match = next(
            (
                name
                for name in sorted(list_dir(tuple(resolved)))
                if collision_key(name) == collision_key(component)
            ),
            None,
        )
        if match is not None and match != component:
            notes.append(f"used existing folder {match!r} instead of {component!r}")
            component = match
        resolved.append(component)
    return tuple(resolved), tuple(notes)
