#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Turn build_agents.yml's gaiacpp bundle into the files a GitHub release carries.

A release, final or candidate, attaches for each platform the archive Lemonade
installs from and the bare binary inside it, plus the pin file naming each
archive by SHA-256 and one checksum list covering all of them.

Nothing is attached on trust: every archive must be present, match the checksum
the pin file records for this tag, and hold a binary stamped with this release's
version. A release that would carry another build's gaiacpp fails here, before
anything is published.

Usage::

    python util/gaiacpp_release_assets.py --bundle bundle/lemonade \\
        --tag v0.25.4-rc1 --version 0.25.4-rc.1 --out release-assets
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

AGENT_ID = "gaiacpp"
REPO = "amd/gaia"
PINS_NAME = "agent_versions.gaia.json"
SUMS_NAME = "gaiacpp-SHA256SUMS.txt"

# archive -> (name of the binary inside it, name of the bare binary attached)
PLATFORMS = {
    "gaiacpp-windows-x64.zip": ("gaiacpp.exe", "gaiacpp-windows-x64.exe"),
    "gaiacpp-linux-x64.tar.gz": ("gaiacpp", "gaiacpp-linux-x64"),
    "gaiacpp-darwin-arm64.tar.gz": ("gaiacpp", "gaiacpp-darwin-arm64"),
}


class ReleaseAssetError(Exception):
    """The bundle is not this release's gaiacpp."""


def release_files() -> list[str]:
    """Every gaiacpp file a release must carry."""
    names = [PINS_NAME, SUMS_NAME]
    for archive, (_, bare) in PLATFORMS.items():
        names += [archive, bare]
    return sorted(names)


def sha256_hex(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _extract(archive: Path, member: str, dest: Path) -> None:
    try:
        if archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                data = z.read(member)
        else:
            with tarfile.open(archive, "r:gz") as t:
                handle = t.extractfile(member)
                if handle is None:
                    raise KeyError(member)
                data = handle.read()
    except KeyError as e:
        raise ReleaseAssetError(
            f"{archive.name} has no {member} at its root; rebuild it with "
            "cpp/packaging/package_lemonade.py"
        ) from e
    if not data:
        raise ReleaseAssetError(f"{archive.name} holds an empty {member}")
    dest.write_bytes(data)
    dest.chmod(0o755)


def is_stamped(binary: Path, version: str) -> bool:
    """Whether the binary carries ``gaiacpp <version>`` as a whole string."""
    return f"{AGENT_ID} {version}\n".encode() in binary.read_bytes()


def collect(bundle: Path, tag: str, version: str, out: Path) -> list[str]:
    """Verify the bundle and write this release's gaiacpp files into ``out``.

    Returns the names written. Raises ReleaseAssetError naming what is wrong.
    """
    pins_path = bundle / PINS_NAME
    if not pins_path.is_file():
        raise ReleaseAssetError(
            f"{pins_path} is missing; build_agents.yml's release-bundle job "
            "writes it, so re-run that job, then this one"
        )
    pins = json.loads(pins_path.read_text(encoding="utf-8"))
    pinned_tag = pins.get(AGENT_ID, {}).get("version")
    if pinned_tag != tag:
        raise ReleaseAssetError(
            f"{PINS_NAME} pins {pinned_tag!r}, but this release is {tag!r}"
        )
    digests = pins.get("checksums", {}).get("github", {}).get(REPO, {}).get(tag, {})

    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for archive_name, (member, bare_name) in PLATFORMS.items():
        archive = bundle / archive_name
        if not archive.is_file() or archive.stat().st_size == 0:
            raise ReleaseAssetError(
                f"the bundle has no {archive_name}; re-run the failed "
                "'Package' leg of build_agents.yml, then this job"
            )
        actual = "sha256:" + sha256_hex(archive)
        if digests.get(archive_name) != actual:
            raise ReleaseAssetError(
                f"{archive_name} is {actual}, but {PINS_NAME} pins "
                f"{digests.get(archive_name)!r}"
            )
        shutil.copyfile(archive, out / archive_name)
        bare = out / bare_name
        _extract(archive, member, bare)
        if not is_stamped(bare, version):
            raise ReleaseAssetError(
                f"{bare_name} is not stamped '{AGENT_ID} {version}'; it was "
                "built for another version"
            )
        written += [archive_name, bare_name]

    shutil.copyfile(pins_path, out / PINS_NAME)
    written.append(PINS_NAME)
    lines = [f"{sha256_hex(out / name)}  {name}\n" for name in sorted(written)]
    (out / SUMS_NAME).write_text("".join(lines), encoding="utf-8", newline="\n")
    written.append(SUMS_NAME)
    return sorted(written)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--tag", required=True, help="release tag, e.g. v0.25.4-rc1")
    parser.add_argument("--version", required=True, help="stamped version, 0.25.4-rc.1")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        written = collect(args.bundle, args.tag, args.version, args.out)
    except ReleaseAssetError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1
    for name in written:
        print(f"attached {name} ({(args.out / name).stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
