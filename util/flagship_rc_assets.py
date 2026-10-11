#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Package a release candidate's own flagship build for its GitHub pre-release.

The Agent Hub has no pre-release channel, so a core release candidate cannot
publish its flagship there. Instead publish.yml freezes the flagship from the
RC's commit (build_flagship_sidecar.yml) and this script turns those artifacts
into pre-release assets, plus ``gaia-agent-release.json``, the one file an RC
core reads to install them (``gaia.hub.prerelease``). Keep the two in step.

Every binary is checked against the SHA-256 and size its freeze leg recorded.
A required platform that is missing or does not match stops the release.

Usage::

    # assemble the pre-release assets
    python util/flagship_rc_assets.py release --artifacts artifacts \\
        --tag v0.25.0-rc3 --commit "$GITHUB_SHA" --out release-assets

    # stage one platform's binary under its installed name (Windows setup)
    python util/flagship_rc_assets.py stage --artifacts artifacts \\
        --platform win32-x64 --out stage/win-x64
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_INIT = REPO_ROOT / "hub/agents/gaia/python/gaia_agent/__init__.py"
AGENT_YAML = REPO_ROOT / "hub/agents/gaia/python/gaia-agent.yaml"

RELEASE_MANIFEST_NAME = "gaia-agent-release.json"
AGENT_YAML_ASSET_NAME = "gaia-agent.yaml"
SCHEMA_VERSION = 1

REQUIRED_PLATFORMS = ("win32-x64", "darwin-arm64", "linux-x64")
OPTIONAL_PLATFORMS = ("darwin-x64",)

_VERSION_RE = re.compile(r'(?m)^__version__\s*=\s*"([^"]+)"')
_RC_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+-rc[1-9]\d*$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _fail(msg: str) -> None:
    print(f"::error::{msg}", flush=True)
    raise SystemExit(1)


def agent_version(init_py: Path = AGENT_INIT) -> str:
    m = _VERSION_RE.search(init_py.read_text(encoding="utf-8"))
    if not m:
        _fail(f"no __version__ in {init_py}; cannot label the flagship build.")
    return m.group(1)


def collect(artifacts: Path) -> dict[str, tuple[dict, Path]]:
    """Map platform -> (meta, binary path) for every verified sidecar build."""
    found: dict[str, tuple[dict, Path]] = {}
    for meta_path in sorted(artifacts.rglob("sidecar-*.meta.json")):
        records = json.loads(meta_path.read_text(encoding="utf-8"))
        for meta in records:
            if meta.get("component") != "sidecar":
                continue
            platform = meta["platform"]
            binary = meta_path.parent / meta["filename"]
            if not binary.is_file():
                _fail(
                    f"{meta_path} describes {meta['filename']}, but no such file sits "
                    "next to it. The freeze leg's artifact upload is incomplete."
                )
            data = binary.read_bytes()
            got = hashlib.sha256(data).hexdigest()
            want = str(meta.get("sha256", ""))
            if not _SHA_RE.match(want) or got != want or len(data) != meta.get("size"):
                _fail(
                    f"{binary} does not match the hash its freeze leg recorded "
                    f"(recorded sha256={want} size={meta.get('size')}, "
                    f"got sha256={got} size={len(data)}). Refusing to ship it."
                )
            found[platform] = (meta, binary)
    return found


def _require(found: dict, platforms) -> None:
    missing = [p for p in platforms if p not in found]
    if missing:
        _fail(
            f"no flagship build for {', '.join(missing)} (built: "
            f"{', '.join(sorted(found)) or 'none'}). Those freeze legs are required; "
            "re-run the failed 'Freeze sidecar' jobs."
        )


def build_release(
    artifacts: Path, tag: str, commit: str, out: Path, version: str
) -> dict:
    if not _RC_TAG_RE.match(tag):
        _fail(f"'{tag}' is not a release-candidate tag (vX.Y.Z-rcN).")
    found = collect(artifacts)
    _require(found, REQUIRED_PLATFORMS)
    for p in OPTIONAL_PLATFORMS:
        if p not in found:
            print(
                f"::warning::no flagship build for {p} in {tag}; an RC core on {p} "
                "will report that the pre-release has no agent for it.",
                flush=True,
            )

    out.mkdir(parents=True, exist_ok=True)
    platforms = {}
    for platform, (meta, binary) in sorted(found.items()):
        shutil.copyfile(binary, out / meta["filename"])
        platforms[platform] = {
            "filename": meta["filename"],
            "sha256": meta["sha256"],
            "size": meta["size"],
        }
    shutil.copyfile(AGENT_YAML, out / AGENT_YAML_ASSET_NAME)
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "agent": "gaia",
        "agentVersion": version,
        "releaseTag": tag,
        "commit": commit,
        "platforms": platforms,
    }
    (out / RELEASE_MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def stage(artifacts: Path, platform: str, out: Path) -> Path:
    found = collect(artifacts)
    _require(found, [platform])
    meta, binary = found[platform]
    out.mkdir(parents=True, exist_ok=True)
    dest = out / meta["executable"]
    shutil.copyfile(binary, dest)
    dest.chmod(0o755)
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    rel = sub.add_parser("release", help="assemble the pre-release assets")
    rel.add_argument("--artifacts", type=Path, required=True)
    rel.add_argument("--tag", required=True)
    rel.add_argument("--commit", required=True)
    rel.add_argument("--out", type=Path, required=True)

    stg = sub.add_parser("stage", help="stage one platform's verified binary")
    stg.add_argument("--artifacts", type=Path, required=True)
    stg.add_argument(
        "--platform", required=True, choices=REQUIRED_PLATFORMS + OPTIONAL_PLATFORMS
    )
    stg.add_argument("--out", type=Path, required=True)

    args = ap.parse_args(argv)
    if args.cmd == "release":
        manifest = build_release(
            args.artifacts, args.tag, args.commit, args.out, agent_version()
        )
        print(json.dumps(manifest, indent=2))
    else:
        dest = stage(args.artifacts, args.platform, args.out)
        print(f"staged {dest} ({dest.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
