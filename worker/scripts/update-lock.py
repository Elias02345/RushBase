#!/usr/bin/env python3
"""Regenerate ``worker/binaries.lock.json`` (llama.cpp, whisper.cpp fallback) and
``worker/models.lock.json`` revisions from the upstream APIs.

Digests are read from the GitHub Releases API (``digest`` field of every asset) and the
Hugging Face model API (LFS ``oid`` = SHA-256, git commit ``sha`` as revision). Small non-LFS
files (configs, label maps) are downloaded once to compute their SHA-256. The result is meant
to be reviewed as a diff and committed; the worker only ever trusts the committed lock files.

    python worker/scripts/update-lock.py binaries [--tag b11681]
    python worker/scripts/update-lock.py models

Stdlib only, so it runs without the project environment.
"""

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BINARIES_LOCK = ROOT / "binaries.lock.json"
MODELS_LOCK = ROOT / "models.lock.json"
API = "https://api.github.com/repos/ggml-org"

# platform key -> (asset name template, optional runtime bundle template); {tag} is substituted.
LLAMA_ASSETS: dict[str, tuple[str, str | None]] = {
    "linux-x64-cpu": ("llama-{tag}-bin-ubuntu-x64.tar.gz", None),
    "linux-x64-vulkan": ("llama-{tag}-bin-ubuntu-vulkan-x64.tar.gz", None),
    "linux-x64-cuda-12.8": (
        "llama-{tag}-bin-ubuntu-cuda-12.8-x64.tar.gz",
        "cudart-llama-{tag}-bin-ubuntu-cuda-12.8-x64.tar.gz",
    ),
    "linux-x64-cuda-13.4": (
        "llama-{tag}-bin-ubuntu-cuda-13.4-x64.tar.gz",
        "cudart-llama-{tag}-bin-ubuntu-cuda-13.4-x64.tar.gz",
    ),
    "linux-x64-rocm": ("llama-{tag}-bin-ubuntu-rocm-10.0-x64.tar.gz", None),
    "linux-x64-sycl": ("llama-{tag}-bin-ubuntu-sycl-fp16-x64.tar.gz", None),
    "macos-arm64-metal": ("llama-{tag}-bin-macos-arm64.tar.gz", None),
    "windows-x64-cpu": ("llama-{tag}-bin-win-cpu-x64.zip", None),
    "windows-x64-vulkan": ("llama-{tag}-bin-win-vulkan-x64.zip", None),
    "windows-x64-cuda-12.4": (
        "llama-{tag}-bin-win-cuda-12.4-x64.zip",
        "cudart-llama-bin-win-cuda-12.4-x64.zip",
    ),
    "windows-x64-cuda-13.4": (
        "llama-{tag}-bin-win-cuda-13.4-x64.zip",
        "cudart-llama-bin-win-cuda-13.4-x64.zip",
    ),
}
WHISPER_FALLBACK_ASSET = "whisper-bin-ubuntu-x64.tar.gz"

# model id -> (HF repo, [(local name, filename in repo)]); metadata lives in models.lock.json.


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(  # noqa: S310
        url, headers={"User-Agent": "rushbase-update-lock"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        return json.load(response)


def asset_entry(assets: dict[str, dict[str, Any]], name: str) -> dict[str, Any]:
    asset = assets[name]
    digest = asset["digest"]
    if not digest.startswith("sha256:"):
        raise SystemExit(f"{name}: unexpected digest {digest!r}")
    return {
        "name": name,
        "url": asset["browser_download_url"],
        "sha256": digest.removeprefix("sha256:"),
        "size": asset["size"],
    }


def pick_llama_release(tag: str | None) -> dict[str, Any]:
    """The newest release (or ``tag``) that carries every wanted asset."""
    releases = (
        [fetch_json(f"{API}/llama.cpp/releases/tags/{tag}")]
        if tag
        else fetch_json(f"{API}/llama.cpp/releases?per_page=15")
    )
    for release in releases:
        names = {a["name"] for a in release["assets"]}
        wanted = {
            tpl.format(tag=release["tag_name"])
            for pair in LLAMA_ASSETS.values()
            for tpl in pair
            if tpl
        }
        if wanted <= names:
            return release
        print(f"skip {release['tag_name']}: missing {sorted(wanted - names)[:3]}", file=sys.stderr)
    raise SystemExit("no llama.cpp release carries all wanted assets")


def update_binaries(tag: str | None) -> None:
    lock: dict[str, Any] = json.loads(BINARIES_LOCK.read_text()) if BINARIES_LOCK.exists() else {}
    release = pick_llama_release(tag)
    assets = {a["name"]: a for a in release["assets"]}
    resolved = release["tag_name"]
    platforms: dict[str, Any] = {}
    for key, (main_tpl, bundle_tpl) in LLAMA_ASSETS.items():
        entry = asset_entry(assets, main_tpl.format(tag=resolved))
        if bundle_tpl:
            entry["runtime_bundle"] = asset_entry(assets, bundle_tpl.format(tag=resolved))
        platforms[key] = entry
    lock["llama.cpp"] = {
        "tag": resolved,
        "published_at": release["published_at"],
        "platforms": platforms,
    }
    whisper_releases = fetch_json(f"{API}/whisper.cpp/releases?per_page=10")
    existing_whisper = lock.get("whisper.cpp", {})
    fallback = next(
        (
            (r, {a["name"]: a for a in r["assets"]})
            for r in whisper_releases
            if WHISPER_FALLBACK_ASSET in {a["name"] for a in r["assets"]}
        ),
        None,
    )
    lock["whisper.cpp"] = {
        "tag": existing_whisper.get("tag", "v1.9.5"),
        "note": "No upstream GPU binaries: CI builds publish Rushbase release assets (see "
        ".github/workflows/whisper-build.yml); `platforms` is filled from those.",
        "platforms": existing_whisper.get("platforms", {}),
        "upstream_cpu_fallback": (
            {"release": fallback[0]["tag_name"], **asset_entry(fallback[1], WHISPER_FALLBACK_ASSET)}
            if fallback
            else None
        ),
    }
    BINARIES_LOCK.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    print(f"llama.cpp {resolved}: {len(platforms)} platforms written to {BINARIES_LOCK.name}")


def hf_files(repo: str) -> tuple[str, dict[str, dict[str, Any]]]:
    info = fetch_json(f"https://huggingface.co/api/models/{repo}?blobs=true")
    return info["sha"], {s["rfilename"]: s for s in info["siblings"]}


def sha256_of_url(url: str) -> str:
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310
        while chunk := response.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def update_models() -> None:
    """Refresh revision, size and SHA-256 of every file listed in ``models.lock.json``.

    The set of models and files is curated by hand in the lock file; this only pins them.
    """
    lock = json.loads(MODELS_LOCK.read_text())
    cache: dict[str, tuple[str, dict[str, dict[str, Any]]]] = {}
    for model_id, model in lock["models"].items():
        for file in model["files"]:
            repo = file["repo"]
            if repo not in cache:
                cache[repo] = hf_files(repo)
            revision, siblings = cache[repo]
            sibling = siblings.get(file["filename"])
            if sibling is None:
                raise SystemExit(f"{model_id}: {repo}/{file['filename']} does not exist")
            file["revision"] = revision
            file["size"] = sibling["size"]
            lfs = sibling.get("lfs")
            if lfs:
                file["sha256"] = lfs["sha256"]
            elif sibling["size"] <= 8 << 20:
                url = f"https://huggingface.co/{repo}/resolve/{revision}/{file['filename']}"
                file["sha256"] = sha256_of_url(url)
            else:
                raise SystemExit(f"{model_id}: {file['filename']} is large but not LFS")
        print(f"{model_id}: pinned")
    MODELS_LOCK.write_text(json.dumps(lock, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="what", required=True)
    binaries = sub.add_parser("binaries")
    binaries.add_argument("--tag", help="pin this llama.cpp release instead of the newest")
    sub.add_parser("models")
    args = parser.parse_args()
    if args.what == "binaries":
        update_binaries(args.tag)
    else:
        update_models()


if __name__ == "__main__":
    main()
