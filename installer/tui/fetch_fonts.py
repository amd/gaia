#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Download the IBM Plex Mono release and stage the faces the Windows setup bundles.

The Windows setup installs these per-user so its Windows Terminal profile has
the font it names. Every expected digest -- the release zip and each file taken
from it -- comes from the COMMITTED ``installer/tui/fonts/fonts.lock.json``,
never from the host that served the bytes. A mismatch is a hard stop and leaves
nothing staged; there is no unverified path.

Usage::

    python installer/tui/fetch_fonts.py --out dist/fonts
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

LOCK = Path(__file__).resolve().parent / "fonts" / "fonts.lock.json"
USER_AGENT = "gaia-installer-build/1.0"


def load_lock(path: Path = LOCK) -> dict:
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
        missing = [
            k
            for k in ("family", "release", "url", "size", "sha256", "license", "fonts")
            if k not in lock
        ]
        if missing:
            raise KeyError(", ".join(missing))
        return lock
    except (OSError, json.JSONDecodeError, KeyError) as e:
        raise SystemExit(
            f"could not read the font pin from {path}: {e}\n"
            f"It must name the release url, its size and sha256, the licence member "
            f"and each bundled face. See installer/tui/fonts/README.md."
        ) from e


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(
            req, timeout=300
        ) as r:  # noqa: S310 - pinned https url
            return r.read()
    except (urllib.error.URLError, TimeoutError) as e:
        raise SystemExit(
            f"download of {url} failed: {e}\n"
            f"GitHub must be reachable to stage the fonts for the Windows setup."
        ) from e


def stage(archive: bytes, lock: dict, out: Path) -> list[Path]:
    """Verify *archive* against *lock* and write the pinned members into *out*."""
    got = _sha256(archive)
    if len(archive) != lock["size"] or got != lock["sha256"]:
        raise SystemExit(
            f"{lock['release']} does not match {LOCK.name}\n"
            f"  expected : {lock['size']} bytes, sha256 {lock['sha256']}\n"
            f"  got      : {len(archive)} bytes, sha256 {got}\n"
            f"Nothing was staged. Re-run to retry a corrupt transfer; if it reproduces, "
            f"the release asset changed upstream -- re-pin deliberately, do not work around it."
        )

    entries = [*lock["fonts"], lock["license"]]
    staged: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for entry in entries:
            try:
                data = zf.read(entry["member"])
            except KeyError as e:
                raise SystemExit(
                    f"{lock['release']} has no member {entry['member']} -- {LOCK.name} "
                    f"is out of date for this release."
                ) from e
            if _sha256(data) != entry["sha256"]:
                raise SystemExit(
                    f"{entry['member']} in {lock['release']} does not match the sha256 "
                    f"pinned in {LOCK.name}. Nothing was staged."
                )
            staged[entry["filename"]] = data

    # Written only once every member has verified, so a failure stages nothing.
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in staged.items():
        dest = out / name
        dest.write_bytes(data)
        written.append(dest)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--out", required=True, type=Path, help="directory to stage the fonts into"
    )
    args = ap.parse_args()

    lock = load_lock()
    print(f"fetching {lock['release']} from {lock['url']}")
    for path in stage(download(lock["url"]), lock, args.out):
        print(f"  verified {path.name} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
