# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Package built C++ agent binaries into Agent Hub ``dist/`` artifacts.

For the current platform, this script locates each C++ agent's compiled
executable in the CMake build tree and produces, per agent::

    dist/<id>/
        <id>-<platform>[.exe]   the binary, renamed to the manifest name
        gaia-agent.yaml          the agent's packaging manifest (copied verbatim)
        checksums.sha256         "<sha256>  <binary-filename>"

The packaged binary filename is dictated by ``cpp.binaries.<platform>`` in each
``cpp/agents/<id>/gaia-agent.yaml``; if a target is built but its manifest does
not name the current platform, or the produced filename would not match the
manifest, the script fails loudly (per CLAUDE.md — no silent fallbacks).

Invoked by ``.github/workflows/build_agents.yml`` once per matrix leg, e.g.::

    python cpp/packaging/package_agents.py \
        --platform linux-x64 \
        --build-dir cpp/build \
        --agents-dir cpp/agents \
        --out dist

Only PyYAML is required beyond the standard library.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional

import yaml

# Map a manifest ``id`` to the CMake executable target that builds it. When you
# add a new C++ agent, add an entry here plus a cpp/agents/<id>/gaia-agent.yaml.
TARGET_BY_ID: Dict[str, str] = {
    "health": "health_agent",
    "wifi": "wifi_agent",
    "process": "process_agent",
    "security-demo": "security_demo",
    "vlm": "vlm_agent",
}

# Matrix platform triples -> executable suffix.
PLATFORM_EXT: Dict[str, str] = {
    "win-x64": ".exe",
    "win-arm64": ".exe",
    "linux-x64": "",
    "linux-arm64": "",
    "darwin-x64": "",
    "darwin-arm64": "",
}

# Build subdirectories that never contain shippable executables.
_SKIP_DIRS = {"CMakeFiles", "_deps", "vcpkg_installed", "Testing"}


class PackagingError(RuntimeError):
    """Raised when an agent cannot be packaged. Message names what to fix."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_binary(build_dir: Path, target: str, ext: str) -> Optional[Path]:
    """Locate the built executable for ``target`` under ``build_dir``.

    Handles both single-config generators (``build/security_demo``) and
    multi-config generators (``build/Release/health_agent.exe``). Returns the
    most recently modified match so a stale Debug build never shadows Release.
    """
    wanted = f"{target}{ext}"
    matches: List[Path] = []
    for candidate in build_dir.rglob(wanted):
        if not candidate.is_file():
            continue
        if any(part in _SKIP_DIRS for part in candidate.relative_to(build_dir).parts):
            continue
        matches.append(candidate)
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def _load_manifest(manifest_path: Path) -> dict:
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise PackagingError(f"Could not read manifest {manifest_path}: {e}") from e
    if not isinstance(data, dict):
        raise PackagingError(
            f"Manifest {manifest_path} is not a YAML mapping. "
            f"See https://amd-gaia.ai/docs/spec/agent-hub-restructure."
        )
    return data


def package_agent(
    *,
    agent_dir: Path,
    platform: str,
    build_dir: Path,
    out_dir: Path,
) -> Optional[Path]:
    """Package one agent for ``platform``. Returns its dist dir, or ``None`` if
    the agent does not target this platform (an expected skip, not an error)."""
    manifest_path = agent_dir / "gaia-agent.yaml"
    if not manifest_path.exists():
        raise PackagingError(
            f"No gaia-agent.yaml in {agent_dir}. Every C++ agent needs a "
            f"packaging manifest. See cpp/agents/README.md."
        )

    manifest = _load_manifest(manifest_path)
    agent_id = manifest.get("id")
    if not agent_id:
        raise PackagingError(f"Manifest {manifest_path} has no 'id'.")

    cpp = manifest.get("cpp") or {}
    binaries = cpp.get("binaries") or {}
    expected_name = binaries.get(platform)
    if not expected_name:
        # This agent intentionally does not target the current platform.
        return None

    if agent_id not in TARGET_BY_ID:
        raise PackagingError(
            f"Manifest id {agent_id!r} ({manifest_path}) has no CMake target in "
            f"TARGET_BY_ID. Add a mapping in cpp/packaging/package_agents.py."
        )

    ext = PLATFORM_EXT[platform]
    derived_name = f"{agent_id}-{platform}{ext}"
    if expected_name != derived_name:
        raise PackagingError(
            f"{manifest_path}: cpp.binaries[{platform!r}] is {expected_name!r} "
            f"but packaging produces {derived_name!r}. Make them match "
            f"(<id>-<platform>{ext})."
        )

    target = TARGET_BY_ID[agent_id]
    binary = _find_binary(build_dir, target, ext)
    if binary is None:
        raise PackagingError(
            f"Built binary for target {target!r} (agent {agent_id!r}) not found "
            f"under {build_dir}. Did the CMake build for {platform} succeed?"
        )

    dest_dir = out_dir / agent_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_binary = dest_dir / expected_name
    shutil.copy2(binary, dest_binary)
    if ext == "":
        dest_binary.chmod(0o755)

    shutil.copy2(manifest_path, dest_dir / "gaia-agent.yaml")

    digest = _sha256(dest_binary)
    (dest_dir / "checksums.sha256").write_text(
        f"{digest}  {expected_name}\n", encoding="utf-8"
    )

    print(
        f"[package] {agent_id:14} {platform:12} -> {dest_binary} "
        f"({dest_binary.stat().st_size} bytes, sha256={digest[:12]}…)"
    )
    return dest_dir


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform",
        required=True,
        choices=sorted(PLATFORM_EXT),
        help="Target platform triple for this matrix leg.",
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=Path("cpp/build"),
        help="CMake build directory containing the compiled binaries.",
    )
    parser.add_argument(
        "--agents-dir",
        type=Path,
        default=Path("cpp/agents"),
        help="Directory of per-agent manifest folders.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("dist"),
        help="Output directory for the packaged artifacts.",
    )
    args = parser.parse_args(argv)

    if not args.build_dir.is_dir():
        print(f"error: build dir not found: {args.build_dir}", file=sys.stderr)
        return 2
    if not args.agents_dir.is_dir():
        print(f"error: agents dir not found: {args.agents_dir}", file=sys.stderr)
        return 2

    agent_dirs = sorted(p for p in args.agents_dir.iterdir() if p.is_dir())
    if not agent_dirs:
        print(f"error: no agent manifests under {args.agents_dir}", file=sys.stderr)
        return 2

    packaged = 0
    for agent_dir in agent_dirs:
        result = package_agent(
            agent_dir=agent_dir,
            platform=args.platform,
            build_dir=args.build_dir,
            out_dir=args.out,
        )
        if result is not None:
            packaged += 1

    if packaged == 0:
        print(
            f"error: no agents targeted platform {args.platform!r}. "
            f"Check cpp.binaries in the manifests.",
            file=sys.stderr,
        )
        return 1

    print(f"[package] done: {packaged} agent(s) packaged for {args.platform}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
