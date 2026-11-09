# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Package gaia-cpp as a Lemonade agent-host release archive.

Lemonade installs an agent from a GitHub release asset pinned by SHA-256 in its
``agent_versions.json``: ``<id>-<os>-<arch>.tar.gz`` (``.zip`` on Windows) with
the binary at the archive root. This script builds that asset from a CMake
build tree and prints the checksum entry Lemonade pins::

    python cpp/packaging/package_lemonade.py --build-dir cpp/build \\
        --os linux --arch x64 --version v0.1.0 --out dist/lemonade

Outputs ``dist/lemonade/gaia-cpp-linux-x64.tar.gz`` and merges its checksum into
``dist/lemonade/agent_versions.gaia.json``.
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

AGENT_ID = "gaia-cpp"
REPO = "amd/gaia"
OSES = ("linux", "darwin", "windows")
ARCHES = ("x64", "arm64")
REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRA_FILES = ("LICENSE.md", "THIRD_PARTY_NOTICES.md")


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


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--build-dir",
        type=Path,
        required=True,
        help="CMake build tree holding gaia-cpp",
    )
    ap.add_argument("--os", choices=OSES, required=True)
    ap.add_argument("--arch", choices=ARCHES, required=True)
    ap.add_argument("--version", required=True, help="release tag, e.g. v0.1.0")
    ap.add_argument("--out", type=Path, default=Path("dist/lemonade"))
    args = ap.parse_args()

    exe = AGENT_ID + (".exe" if args.os == "windows" else "")
    candidates = [args.build_dir / exe, *args.build_dir.glob(f"*/{exe}")]
    binary = next((c for c in candidates if c.is_file()), None)
    if binary is None:
        raise SystemExit(
            f"error: no {exe} under {args.build_dir}. Build it first: "
            f"cmake --build {args.build_dir} --target gaia-cpp"
        )

    archive = build_archive(binary, args.os, args.out / asset_name(args.os, args.arch))
    digest = sha256(archive)

    pins_path = args.out / "agent_versions.gaia.json"
    pins = json.loads(pins_path.read_text("utf-8")) if pins_path.is_file() else {}
    pins.setdefault(AGENT_ID, {})["version"] = args.version
    assets = (
        pins.setdefault("checksums", {}).setdefault("github", {}).setdefault(REPO, {})
    )
    assets.setdefault(args.version, {})[archive.name] = digest
    pins_path.write_text(json.dumps(pins, indent=2) + "\n", "utf-8")

    print(f"{archive}  {digest}")
    print(f"pins: {pins_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
