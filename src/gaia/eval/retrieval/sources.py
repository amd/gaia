# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Download-on-demand corpora, pinned by URL and verified by sha256.

Nothing fetched here is ever committed: files land in the cache directory and
every read re-checks the checksum recorded in ``eval/retrieval/sources.json``.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

CACHE_ENV = "GAIA_RETRIEVAL_CACHE"
REPO_ROOT = Path(__file__).resolve().parents[4]
_USER_AGENT = "Mozilla/5.0 (gaia-eval-retrieval; +https://github.com/amd/gaia)"


class SourceError(RuntimeError):
    """A corpus file could not be fetched or failed its checksum."""


def manifest_path() -> Path:
    return REPO_ROOT / "eval" / "retrieval" / "sources.json"


def load_manifest() -> Dict[str, dict]:
    path = manifest_path()
    if not path.is_file():
        raise SourceError(
            f"Corpus manifest not found at {path}. `gaia eval retrieval` needs a "
            "source checkout of amd/gaia (the manifest and labels ship in eval/)."
        )
    return json.loads(path.read_text(encoding="utf-8"))["sources"]


def cache_dir() -> Path:
    override = os.environ.get(CACHE_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".gaia" / "cache" / "eval-retrieval"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _entry(manifest: Dict[str, dict], source: str, rel: str) -> dict:
    if source not in manifest:
        raise SourceError(f"Unknown corpus source {source!r} in {manifest_path()}")
    files = manifest[source]["files"]
    if rel not in files:
        raise SourceError(
            f"{source}/{rel} is not listed in {manifest_path()}. Add it with its "
            "pinned URL and sha256 before using it."
        )
    return files[rel]


def local_path(source: str, rel: str) -> Path:
    return cache_dir() / source / rel


def fetch(
    source: str,
    rel: str,
    manifest: Optional[Dict[str, dict]] = None,
    offline: bool = False,
) -> Path:
    """Return the verified local path of ``source/rel``, downloading it if absent.

    A file already on disk with the wrong checksum is an error — it is never
    silently replaced, because that would hide a corrupted or edited corpus.
    """
    manifest = manifest or load_manifest()
    entry = _entry(manifest, source, rel)
    dest = local_path(source, rel)
    if not dest.exists():
        if offline:
            raise SourceError(
                f"{dest} is not downloaded and --offline was given. Run once "
                "without --offline (or `gaia eval retrieval --fetch-only`) to "
                f"download it from {entry['url']}."
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        request = urllib.request.Request(
            entry["url"], headers={"User-Agent": _USER_AGENT}
        )
        try:
            with (
                urllib.request.urlopen(request, timeout=300) as response,
                open(part, "wb") as out,
            ):
                for block in iter(lambda: response.read(1 << 20), b""):
                    out.write(block)
        except (urllib.error.URLError, OSError) as e:
            part.unlink(missing_ok=True)
            raise SourceError(
                f"Download failed for {source}/{rel} from {entry['url']}: {e}. "
                "Check the network, or fetch the file by hand into "
                f"{dest} (it must match sha256 {entry['sha256']})."
            ) from e
        part.replace(dest)
    actual = _sha256(dest)
    if actual != entry["sha256"]:
        raise SourceError(
            f"Checksum mismatch for {dest}: expected {entry['sha256']}, got {actual}. "
            f"The upstream file at {entry['url']} changed, or the download is "
            "corrupt. Delete the file to re-download; if upstream really changed, "
            "update its sha256 in eval/retrieval/sources.json and re-check the labels."
        )
    return dest


def fetch_many(
    items: Iterable[tuple],
    offline: bool = False,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    workers: int = 8,
) -> List[Path]:
    """Fetch ``(source, rel)`` pairs concurrently; order of the result matches input."""
    items = list(items)
    manifest = load_manifest()
    results: List[Optional[Path]] = [None] * len(items)
    done = 0
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        futures = {
            pool.submit(fetch, source, rel, manifest, offline): i
            for i, (source, rel) in enumerate(items)
        }
        for future in concurrent.futures.as_completed(futures):
            i = futures[future]
            results[i] = future.result()
            done += 1
            if on_progress:
                on_progress(done, len(items), f"{items[i][0]}/{items[i][1]}")
    return [p for p in results if p is not None]


def source_files(source: str, prefix: str = "") -> List[str]:
    """Manifest-listed relative paths of ``source`` starting with ``prefix``."""
    return sorted(r for r in load_manifest()[source]["files"] if r.startswith(prefix))


def license_notes(sources: Iterable[str]) -> Dict[str, dict]:
    manifest = load_manifest()
    return {
        s: {
            k: manifest[s][k]
            for k in ("title", "homepage", "license")
            if k in manifest[s]
        }
        for s in sorted(set(sources))
    }
