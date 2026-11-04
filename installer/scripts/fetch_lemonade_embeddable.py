#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Download the pinned embeddable Lemonade archive the desktop app ships.

The version comes from ``src/gaia/version.py`` and the digest from
``EMBEDDABLE_SHA256`` in ``src/gaia/llm/lemonade_embedded.py``, never from the
host that served the bytes. A mismatch is a hard stop and leaves nothing staged.
Both are read as source because the build has no ``gaia`` package installed.

Usage::

    python installer/scripts/fetch_lemonade_embeddable.py --platform win-x64 \\
        --out src/gaia/apps/webui/build/vendor/lemonade
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION_PY = REPO_ROOT / "src" / "gaia" / "version.py"
EMBEDDED_PY = REPO_ROOT / "src" / "gaia" / "llm" / "lemonade_embedded.py"
RELEASE_BASE = "https://github.com/lemonade-sdk/lemonade/releases/download"
USER_AGENT = "gaia-installer-build/1.0"

# The vendor key each desktop build uses -> the asset's platform suffix.
ASSET_SUFFIX = {
    "win-x64": "windows-x64.zip",
    "linux-x64": "ubuntu-x64.tar.gz",
    "mac-arm64": "macos-arm64.tar.gz",
}


def lemonade_version(path: Path = VERSION_PY) -> str:
    m = re.search(
        r'^LEMONADE_VERSION = "([^"]+)"', path.read_text(encoding="utf-8"), re.M
    )
    if not m:
        raise SystemExit(f"could not parse LEMONADE_VERSION from {path}")
    return m.group(1)


def pinned_digests(path: Path = EMBEDDED_PY) -> dict[str, str]:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if (
            isinstance(node, ast.AnnAssign)
            and getattr(node.target, "id", "") == "EMBEDDABLE_SHA256"
        ):
            return ast.literal_eval(node.value)
    raise SystemExit(f"could not find EMBEDDABLE_SHA256 in {path}")


def download(url: str, attempts: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(
                request, timeout=300
            ) as response:  # noqa: S310 - pinned https url
                return response.read()
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            print(f"attempt {attempt}/{attempts}: {url} failed: {e}", file=sys.stderr)
            if attempt == attempts:
                raise SystemExit(
                    f"download of {url} failed: {e}\n"
                    f"GitHub must be reachable to build the desktop app."
                ) from e
    raise SystemExit(f"--attempts must be at least 1, got {attempts}")


def stage(platform: str, out: Path, attempts: int = 1) -> Path:
    """Fetch the *platform* archive into *out*, the only archive left there."""
    version = lemonade_version()
    name = f"lemonade-embeddable-{version}-{ASSET_SUFFIX[platform]}"
    expected = pinned_digests().get(name)
    if expected is None:
        raise SystemExit(
            f"no pinned SHA-256 for {name} in EMBEDDABLE_SHA256 "
            f"({EMBEDDED_PY}). Refresh the pins when LEMONADE_VERSION moves; "
            f"the digests are on the v{version} release."
        )
    data = download(f"{RELEASE_BASE}/v{version}/{name}", attempts)
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise SystemExit(
            f"{name} does not match EMBEDDABLE_SHA256\n"
            f"  expected : {expected}\n"
            f"  got      : {actual}\n"
            f"Nothing was staged. Re-run to retry a corrupt transfer; if it "
            f"reproduces, the release asset changed upstream."
        )
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("lemonade-embeddable-*"):
        stale.unlink()
    dest = out / name
    dest.write_bytes(data)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform", required=True, choices=sorted(ASSET_SUFFIX))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--attempts", type=int, default=1)
    args = parser.parse_args()
    dest = stage(args.platform, args.out, args.attempts)
    print(f"staged {dest} ({dest.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
