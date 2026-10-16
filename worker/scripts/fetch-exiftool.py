#!/usr/bin/env python3
"""Install the pinned Image-ExifTool into the project toolchain (no system changes).

The version, URL and SHA-256 come from ``worker/binaries.lock.json`` (the checksum is the one
published on https://exiftool.org/checksums.txt). The archive is verified before extraction.
ExifTool is a Perl program, so ``perl`` must be installed. A wrapper script is written to
``<toolchain>/bin/exiftool``; the worker finds it there automatically.

Usage: ``python3 worker/scripts/fetch-exiftool.py [--toolchain DIR]``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

LOCK = Path(__file__).resolve().parents[1] / "binaries.lock.json"


def find_toolchain() -> Path:
    """Return ``$TOOLCHAIN_DIR`` or the nearest ``.toolchain`` in this tree or a parent."""
    env = os.environ.get("TOOLCHAIN_DIR")
    if env:
        return Path(env)
    for parent in LOCK.parents:
        if (parent / ".toolchain").is_dir():
            return parent / ".toolchain"
    return LOCK.parents[1] / ".toolchain"


def main() -> int:
    """Download, verify and install ExifTool; return the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain", type=Path, default=None)
    args = parser.parse_args()
    toolchain: Path = args.toolchain or find_toolchain()
    spec = json.loads(LOCK.read_text(encoding="utf-8"))["exiftool"]
    perl = shutil.which("perl")
    if perl is None:
        print("error: perl is required to run ExifTool but was not found on PATH", file=sys.stderr)
        return 2
    target = toolchain / "exiftool" / f"Image-ExifTool-{spec['version']}"
    wrapper = toolchain / "bin" / "exiftool"
    if (target / "exiftool").is_file() and wrapper.is_file():
        print(f"exiftool {spec['version']} already installed at {target}")
        return 0
    with tempfile.TemporaryDirectory(prefix="rb-exiftool-") as tmp:
        archive = Path(tmp) / "exiftool.tar.gz"
        with urllib.request.urlopen(spec["url"], timeout=60) as response:  # noqa: S310
            archive.write_bytes(response.read())
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != spec["sha256"]:
            print(f"error: checksum mismatch: {digest} != {spec['sha256']}", file=sys.stderr)
            return 1
        extract = Path(tmp) / "x"
        extract.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(extract, filter="data")
        (source,) = list(extract.iterdir())
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(source), target)
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(f'#!/bin/sh\nexec "{perl}" "{target / "exiftool"}" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    version = subprocess.run([str(wrapper), "-ver"], capture_output=True, text=True, check=True)
    print(f"installed exiftool {version.stdout.strip()} -> {wrapper}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
