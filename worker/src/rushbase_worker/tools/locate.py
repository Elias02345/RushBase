"""Find external executables: explicit setting, then the project toolchain, then PATH."""

import os
import shutil
from pathlib import Path


def toolchain_bin_dirs(toolchain: Path | None = None) -> list[Path]:
    """Return candidate ``bin`` directories of the project toolchain."""
    candidates = [toolchain] if toolchain else []
    env = os.environ.get("TOOLCHAIN_DIR")
    if env:
        candidates.append(Path(env))
    return [c / "bin" for c in candidates]


def locate(name: str, configured: str | None = None, toolchain: Path | None = None) -> str | None:
    """Return the executable path for ``name`` or ``None`` when it cannot be found.

    Resolution order: ``configured`` (absolute path or command name), toolchain ``bin``
    directories (``toolchain`` argument, ``$TOOLCHAIN_DIR``), ``PATH``.
    """
    if configured:
        return shutil.which(configured)
    for directory in toolchain_bin_dirs(toolchain):
        found = shutil.which(name, path=str(directory))
        if found:
            return found
    return shutil.which(name)
