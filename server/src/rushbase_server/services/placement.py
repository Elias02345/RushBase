"""File system side of filing: directory planning and rename-based placement (synchronous).

Everything here works on library-relative paths and touches the file system only through
:mod:`rushbase_core.fsops`. Nothing is ever overwritten; a failed placement is rolled back so
that either every file of a unit moved or none did. The functions are plain synchronous code,
meant to run in a worker thread (a slow network share must not block the event loop).
"""

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from rushbase_core import resolve_in_root
from rushbase_core.filing import (
    DEFAULT_PATH_BUDGET,
    FilingContext,
    Template,
    fit_path_budget,
    reconcile_case,
    render_directory,
)
from rushbase_core.fsops import safe_rename
from rushbase_core.sanitize import collision_key, first_free_index, numbered_name


@dataclass(frozen=True)
class DirectoryPlan:
    """Where an asset (or card unit) would be filed."""

    components: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    fits_budget: bool = True
    adopted: tuple[str, ...] = ()
    """Notes about existing folders whose spelling was adopted (case/normalisation clashes)."""

    @property
    def path(self) -> str:
        """Library-relative target directory."""
        return "/".join(self.components)


def list_directory(root: Path | None) -> Callable[[tuple[str, ...]], list[str]]:
    """Return a ``list_dir`` callback for :func:`reconcile_case` (empty list for missing dirs)."""

    def listing(parts: tuple[str, ...]) -> list[str]:
        if root is None:
            return []
        try:
            return os.listdir(root.joinpath(*parts))
        except OSError:
            return []

    return listing


def plan_directory(
    root: Path | None,
    template: Template,
    ctx: FilingContext,
    fallbacks: Mapping[str, str],
    tail_length: int,
    budget: int = DEFAULT_PATH_BUDGET,
) -> DirectoryPlan:
    """Render ``template`` for ``ctx``, fit the path budget and adopt existing folder spellings."""
    rendered = render_directory(template, ctx, fallbacks)
    fitted, fits = fit_path_budget(rendered.components, tail_length, budget)
    warnings = list(rendered.warnings)
    if fitted != rendered.components:
        warnings.append("shortened path components to fit the path budget")
    if not fits:
        warnings.append(f"path exceeds the budget of {budget} characters")
    reconciled, adopted = reconcile_case(fitted, list_directory(root))
    return DirectoryPlan(reconciled, tuple(warnings), fits, adopted)


@dataclass(frozen=True)
class MoveSpec:
    """One file that has to move: ``src`` is its current library-relative path."""

    file_id: str
    src: str


@dataclass(frozen=True)
class UnitSpec:
    """What :func:`place_unit` moves.

    * ``card_root`` set and ``whole_directory``: the directory is renamed as one unit.
    * ``card_root`` set otherwise: files move individually below a new root directory, keeping
      their structure (the directory holds files of assets that must not move).
    * ``card_root`` unset: files move into the target directory under their own names.
    """

    moves: Sequence[MoveSpec]
    card_root: str | None = None
    whole_directory: bool = False


@dataclass
class Placement:
    """Result of a placement: the final paths, and what is needed to undo it."""

    directory: str
    """Library-relative directory the files (or the card root) ended up in."""
    new_paths: dict[str, str] = field(default_factory=dict)
    """``file_id`` -> new library-relative path."""
    card_root: str | None = None
    """New location of the card root (``None`` for plain files)."""
    renames: list[tuple[str, str]] = field(default_factory=list)
    """Performed ``(src, dst)`` renames in order (directory renames count once)."""
    directories_created: list[str] = field(default_factory=list)


def _basename(rel: str) -> str:
    return PurePosixPath(rel).name


def tail_length_for(spec: UnitSpec) -> int:
    """Length of the longest path below the target directory (used for the path budget)."""
    if spec.card_root is None:
        return max((len(_basename(m.src)) for m in spec.moves), default=0)
    root_name = _basename(spec.card_root)
    deepest = max((len(m.src) - len(spec.card_root) for m in spec.moves), default=0)
    return len(root_name) + 3 + deepest  # allow a numbered suffix such as "_12"


def _taken(directory: Path) -> set[str]:
    try:
        return {collision_key(name) for name in os.listdir(directory)}
    except OSError:
        return set()


def _make_directory(root: Path, rel: str, placement: Placement) -> None:
    """Create ``rel`` (and parents), remembering the created ones for rollback."""
    missing: list[str] = []
    current = PurePosixPath(rel)
    while str(current) not in (".", "") and not resolve_in_root(root, str(current)).exists():
        missing.append(str(current))
        current = current.parent
    resolve_in_root(root, rel).mkdir(parents=True, exist_ok=True)
    placement.directories_created.extend(reversed(missing))


def place_unit(root: Path, spec: UnitSpec, target_dir: str) -> Placement:
    """Rename the files of ``spec`` into ``target_dir`` below ``root``.

    The target directory is created as needed. Name collisions get a ``_2``, ``_3`` ... suffix
    that is shared by all files of the unit so RAW/JPEG/sidecar pairs keep matching names.
    On any error everything already moved is moved back and the error is re-raised.

    Raises:
        FileNotFoundError: a source file vanished.
        rushbase_core.fsops.FileInUseError: the share reported the file as busy/denied.
        OSError: any other file system error.
    """
    placement = Placement(directory=target_dir)
    try:
        _make_directory(root, target_dir, placement)
        if spec.card_root is None:
            _place_files(root, spec, target_dir, placement)
        elif spec.whole_directory:
            _place_directory(root, spec, target_dir, placement)
        else:
            _place_structured(root, spec, target_dir, placement)
    except BaseException:
        rollback(root, placement)
        raise
    return placement


def _place_files(root: Path, spec: UnitSpec, target_dir: str, placement: Placement) -> None:
    names = [_basename(m.src) for m in spec.moves]
    index = first_free_index(names, _taken(resolve_in_root(root, target_dir)))
    for move in spec.moves:
        dst = f"{target_dir}/{numbered_name(_basename(move.src), index)}"
        _rename(root, move.src, dst, placement)
        placement.new_paths[move.file_id] = dst


def _free_directory_name(parent: Path, name: str) -> str:
    taken = _taken(parent)
    index = 1
    while collision_key(name if index == 1 else f"{name}_{index}") in taken:
        index += 1
    return name if index == 1 else f"{name}_{index}"


def _place_directory(root: Path, spec: UnitSpec, target_dir: str, placement: Placement) -> None:
    assert spec.card_root is not None
    name = _free_directory_name(resolve_in_root(root, target_dir), _basename(spec.card_root))
    new_root = f"{target_dir}/{name}"
    _rename(root, spec.card_root, new_root, placement)
    placement.card_root = new_root
    for move in spec.moves:
        placement.new_paths[move.file_id] = new_root + move.src[len(spec.card_root) :]


def _place_structured(root: Path, spec: UnitSpec, target_dir: str, placement: Placement) -> None:
    assert spec.card_root is not None
    name = _free_directory_name(resolve_in_root(root, target_dir), _basename(spec.card_root))
    new_root = f"{target_dir}/{name}"
    _make_directory(root, new_root, placement)
    placement.card_root = new_root
    for move in spec.moves:
        dst = new_root + move.src[len(spec.card_root) :]
        _rename(root, move.src, dst, placement)
        placement.new_paths[move.file_id] = dst


def _rename(root: Path, src: str, dst: str, placement: Placement) -> None:
    safe_rename(resolve_in_root(root, src), resolve_in_root(root, dst))
    placement.renames.append((src, dst))


def rollback(root: Path, placement: Placement) -> None:
    """Move everything back and remove directories created by the placement (best effort)."""
    for src, dst in reversed(placement.renames):
        try:
            safe_rename(resolve_in_root(root, dst), resolve_in_root(root, src))
        except OSError:
            continue  # nothing more can be done; the journal still names both locations
    placement.renames.clear()
    for rel in reversed(placement.directories_created):
        try:
            resolve_in_root(root, rel).rmdir()
        except OSError:
            continue
    placement.directories_created.clear()
