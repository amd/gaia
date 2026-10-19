#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Publish an npm package unless that exact version is already on the registry.

npm versions are immutable, so re-running a release after the npm step
succeeded used to fail on "cannot publish over the previously published
version". This makes the step safe to re-run:

- the version is not on the registry: publish it;
- it is there with the same tarball integrity: skip, nothing to do;
- it is there with different bytes: fail, because the registry already holds a
  different build under this version and that cannot be corrected.

Usage (from the package directory, after the build)::

    python util/npm_publish_once.py --dist-tag next -- --access public --provenance --ignore-scripts
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

REGISTRY = "https://registry.npmjs.org"


# npm is npm.cmd on Windows; shell=False needs the real name there.
NPM = "npm.cmd" if sys.platform == "win32" else "npm"


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [NPM, *cmd], cwd=cwd, capture_output=True, text=True, check=False
    )


def local_integrity(cwd: Path) -> tuple[str, str, str]:
    """(name, version, integrity) of the tarball `npm publish` would upload."""
    res = run(["pack", "--dry-run", "--json", "--ignore-scripts"], cwd)
    if res.returncode != 0:
        raise RuntimeError(f"npm pack --dry-run failed:\n{res.stderr}")
    info = json.loads(res.stdout)[0]
    return info["name"], info["version"], info["integrity"]


def registry_integrity(
    name: str, version: str, fetch: Callable[[str], tuple[int, bytes]]
) -> str | None:
    """Integrity of ``name@version`` on the registry, or None when not published.

    Asks the registry directly rather than `npm view`, which can answer from a
    stale local cache and report a published version as missing. Only a 404
    means "not published"; any other non-200 is an error.
    """
    url = f"{REGISTRY}/{name.replace('/', '%2f')}/{version}"
    status, body = fetch(url)
    if status == 404:
        return None
    if status != 200:
        raise RuntimeError(f"GET {url} returned HTTP {status}: {body[:300]!r}")
    integrity = json.loads(body).get("dist", {}).get("integrity")
    if not integrity:
        raise RuntimeError(f"GET {url} returned no dist.integrity")
    return integrity


def http_get(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def decide(local: str, remote: str | None) -> str:
    """'publish', 'skip', or 'conflict'."""
    if remote is None:
        return "publish"
    return "skip" if remote == local else "conflict"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    ap.add_argument("--dir", type=Path, default=Path.cwd())
    ap.add_argument("--dist-tag", required=True)
    ap.add_argument("publish_args", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    extra = [a for a in args.publish_args if a != "--"]

    try:
        name, version, local = local_integrity(args.dir)
        spec = f"{name}@{version}"
        remote = registry_integrity(name, version, http_get)
    except RuntimeError as e:
        print(f"::error::{e}")
        return 1

    action = decide(local, remote)
    if action == "skip":
        print(f"{spec} is already on npm with the same tarball ({local}); skipping.")
        return 0
    if action == "conflict":
        print(
            f"::error::{spec} is already on npm with different bytes (registry "
            f"{remote}, this build {local}). npm versions are immutable, so this "
            "build cannot ship under that version. Re-run only the failed jobs "
            "of the run that published it, or release a new version."
        )
        return 1

    res = subprocess.run(
        [NPM, "publish", "--tag", args.dist_tag, *extra],
        cwd=args.dir,
        check=False,
    )
    if res.returncode != 0:
        print(f"::error::npm publish {spec} failed (exit {res.returncode}).")
        return res.returncode
    print(f"Published {spec} under the '{args.dist_tag}' dist-tag.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
