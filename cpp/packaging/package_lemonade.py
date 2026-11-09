# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Package gaiacpp as a Lemonade agent-host release archive.

Lemonade installs an agent from a GitHub release asset pinned by SHA-256 in its
``agent_versions.json``: ``<id>-<os>-<arch>.tar.gz`` (``.zip`` on Windows) with
the binary at the archive root. This script builds that asset from a CMake
build tree and prints the checksum entry Lemonade pins::

    python cpp/packaging/package_lemonade.py --build-dir cpp/build \\
        --os linux --arch x64 --version v0.1.0 --out dist/lemonade

Outputs ``dist/lemonade/gaiacpp-linux-x64.tar.gz`` and merges its checksum into
``dist/lemonade/agent_versions.gaia.json``.

Each platform is built on its own machine, so a release has one pin file per
platform. ``--merge`` joins them into the single file a release publishes::

    python cpp/packaging/package_lemonade.py --out bundle/lemonade \\
        --merge linux/agent_versions.gaia.json windows/agent_versions.gaia.json
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

AGENT_ID = "gaiacpp"
REPO = "amd/gaia"
OSES = ("linux", "darwin", "windows")
ARCHES = ("x64", "arm64")
REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRA_FILES = ("LICENSE.md", "THIRD_PARTY_NOTICES.md")
PINS_NAME = "agent_versions.gaia.json"


def asset_name(os_name: str, arch: str) -> str:
    return f"{AGENT_ID}-{os_name}-{arch}" + (
        ".zip" if os_name == "windows" else ".tar.gz"
    )


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def build_archive(binary: Path, os_name: str, out: Path) -> Path:
    exe_name = AGENT_ID + (".exe" if os_name == "windows" else "")
    extras = [REPO_ROOT / name for name in EXTRA_FILES]
    for extra in extras:
        if not extra.is_file():
            raise SystemExit(
                f"error: {extra} is missing; the archive must carry the license files"
            )
    out.parent.mkdir(parents=True, exist_ok=True)
    if os_name == "windows":
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(binary, exe_name)
            for extra in extras:
                z.write(extra, extra.name)
    else:
        with tarfile.open(out, "w:gz") as t:
            info = t.gettarinfo(str(binary), exe_name)
            info.mode = 0o755
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with binary.open("rb") as f:
                t.addfile(info, f)
            for extra in extras:
                data = extra.read_bytes()
                ti = tarfile.TarInfo(extra.name)
                ti.size = len(data)
                ti.mtime = info.mtime
                ti.mode = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH
                t.addfile(ti, io.BytesIO(data))
    return out


def add_pin(pins: dict, version: str, asset: str, digest: str) -> dict:
    """Record one asset's checksum in ``pins`` and return it."""
    pins.setdefault(AGENT_ID, {})["version"] = version
    assets = (
        pins.setdefault("checksums", {}).setdefault("github", {}).setdefault(REPO, {})
    )
    assets.setdefault(version, {})[asset] = digest
    return pins


def merge_pins(pin_sets: list[dict]) -> dict:
    """Join per-platform pin files into one that lists every platform's asset.

    Raises ValueError when the files name different versions or give two
    checksums for one asset: either means the inputs are not one release.
    """
    if not pin_sets:
        raise ValueError("no pin files to merge")
    merged: dict = {}
    for pins in pin_sets:
        version = pins.get(AGENT_ID, {}).get("version")
        if not version:
            raise ValueError(f"pin file has no {AGENT_ID}.version")
        known = merged.get(AGENT_ID, {}).get("version", version)
        if version != known:
            raise ValueError(f"pin files name two versions: {known} and {version}")
        merged.setdefault(AGENT_ID, {})["version"] = version
        for host, repos in pins.get("checksums", {}).items():
            for repo, versions in repos.items():
                for ver, assets in versions.items():
                    target = (
                        merged.setdefault("checksums", {})
                        .setdefault(host, {})
                        .setdefault(repo, {})
                        .setdefault(ver, {})
                    )
                    for asset, digest in assets.items():
                        if target.setdefault(asset, digest) != digest:
                            raise ValueError(
                                f"{asset} has two checksums: "
                                f"{target[asset]} and {digest}"
                            )
    return merged


def write_pins(pins: dict, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    pins_path = out / PINS_NAME
    pins_path.write_text(json.dumps(pins, indent=2) + "\n", "utf-8")
    return pins_path


def run_merge(sources: list[Path], out: Path) -> int:
    pin_sets = []
    for source in sources:
        if not source.is_file():
            raise SystemExit(f"error: pin file {source} does not exist")
        try:
            pin_sets.append(json.loads(source.read_text("utf-8")))
        except json.JSONDecodeError as e:
            raise SystemExit(f"error: {source} is not valid JSON: {e}") from e
    try:
        merged = merge_pins(pin_sets)
    except ValueError as e:
        raise SystemExit(
            f"error: cannot merge {', '.join(map(str, sources))}: {e}"
        ) from e
    print(f"pins: {write_pins(merged, out)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--build-dir", type=Path, help="CMake build tree holding gaiacpp")
    ap.add_argument("--os", choices=OSES)
    ap.add_argument("--arch", choices=ARCHES)
    ap.add_argument("--version", help="release tag, e.g. v0.1.0")
    ap.add_argument("--out", type=Path, default=Path("dist/lemonade"))
    ap.add_argument(
        "--merge",
        type=Path,
        nargs="+",
        metavar="PINS",
        help=f"join these per-platform {PINS_NAME} files into --out instead of packaging",
    )
    args = ap.parse_args(argv)

    if args.merge:
        return run_merge(args.merge, args.out)

    missing = [
        flag
        for flag, value in (
            ("--build-dir", args.build_dir),
            ("--os", args.os),
            ("--arch", args.arch),
            ("--version", args.version),
        )
        if value is None
    ]
    if missing:
        ap.error(f"packaging needs {', '.join(missing)}")

    exe = AGENT_ID + (".exe" if args.os == "windows" else "")
    candidates = [args.build_dir / exe, *args.build_dir.glob(f"*/{exe}")]
    binary = next((c for c in candidates if c.is_file()), None)
    if binary is None:
        raise SystemExit(
            f"error: no {exe} under {args.build_dir}. Build it first: "
            f"cmake --build {args.build_dir} --target gaiacpp"
        )

    archive = build_archive(binary, args.os, args.out / asset_name(args.os, args.arch))
    digest = sha256(archive)

    pins_path = args.out / PINS_NAME
    pins = json.loads(pins_path.read_text("utf-8")) if pins_path.is_file() else {}
    pins_path = write_pins(add_pin(pins, args.version, archive.name, digest), args.out)

    print(f"{archive}  {digest}")
    print(f"pins: {pins_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
