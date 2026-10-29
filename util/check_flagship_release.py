#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fail a final core release whose flagship agent was not built from it.

Users get the flagship (`gaia-agent`) from the Agent Hub (`gaia init`,
`gaia hub install gaia`) and from the committed pin in
hub/agents/gaia/npm/binaries.lock.json (the Windows setup, npm). Both are
filled by release_agent_gaia.yml, a separate tag (`agent-pkg-gaia-vX.Y.Z`), so
nothing tied them to a core release: v0.25.0 was prepared with the flagship
still at 0.2.0, built 445 commits earlier.

A final passes only when all of these hold for the committed flagship
version V:

1. the lock pins sidecar V with real hashes;
2. tag ``agent-pkg-gaia-vV`` exists and nothing the binary is frozen from
   differs between it and the release commit;
3. the hub publishes V as the flagship's latest, with the lock's hashes.

Usage::

    python util/check_flagship_release.py            # check HEAD (pre-tag)
    python util/check_flagship_release.py --ref v0.25.0
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "hub/agents/gaia/python/gaia-agent.yaml"
LOCK = "hub/agents/gaia/npm/binaries.lock.json"
DEFAULT_HUB_URL = "https://hub.amd-gaia.ai"
TAG_PREFIX = "agent-pkg-gaia-v"
# darwin-x64 is left out on purpose: release_agent_gaia.yml publishes it best-effort.
REQUIRED_PLATFORMS = ("win32-x64", "darwin-arm64", "linux-x64")

# What the flagship freeze bundles. Tests, eval data and the web frontend are
# not in it; bundled skill prompts (*.md) and gaia.apps.llm are, so they stay.
FROZEN_INPUTS = (
    "src/gaia",
    ":(exclude)src/gaia/apps/webui",
    "hub/agents/gaia/python",
    ":(exclude)hub/agents/gaia/python/tests",
    ":(exclude)hub/agents/gaia/python/eval_baselines",
    "hub/agents/chat/python",
    ":(exclude)hub/agents/chat/python/tests",
    "setup.py",
)

# The WAF in front of hub.amd-gaia.ai 403s urllib's default User-Agent.
USER_AGENT = "gaia-release/1 (+https://github.com/amd/gaia)"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"(?m)^version:\s*['\"]?([^'\"\s#]+)")

HOW_TO_FIX = (
    "To release the flagship from this commit: bump it with "
    "hub/agents/gaia/python/packaging/stamp_version.py (plus its CHANGELOG), "
    "dispatch release_components.yml if the terminal-hub version the lock "
    f"pins is not on the hub yet, tag {TAG_PREFIX}<version> on the release "
    "commit, approve "
    "release_agent_gaia.yml, merge the 'pin flagship agent <version>'s published "
    "hashes' PR it opens (branch flagship-lock/v<version>), and tag the "
    "core release on that commit. See the gaia-release skill."
)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout


def _read_at(ref: str, path: str) -> str:
    try:
        return _git("show", f"{ref}:{path}")
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"cannot read {path} at {ref}: {e.stderr.strip()}") from e


def flagship_version(manifest_text: str) -> str:
    m = _VERSION_RE.search(manifest_text)
    if not m:
        raise SystemExit(f"{MANIFEST} has no top-level version.")
    return m.group(1)


def check_lock(lock: dict, version: str) -> list[str]:
    """Problems with the committed sidecar pin for flagship *version*."""
    sidecar = (lock.get("components") or {}).get("sidecar") or {}
    problems = []
    if sidecar.get("componentVersion") != version:
        problems.append(
            f"{LOCK} pins sidecar {sidecar.get('componentVersion')!r}, but the "
            f"flagship is {version}. The Windows setup and npm package would ship "
            "a different build than the hub."
        )
    if (
        not str(sidecar.get("baseUrl", ""))
        .rstrip("/")
        .endswith(f"agents/gaia/{version}")
    ):
        problems.append(
            f"{LOCK} sidecar baseUrl does not point at agents/gaia/{version}."
        )
    platforms = sidecar.get("platforms") or {}
    for p in REQUIRED_PLATFORMS:
        sha = str((platforms.get(p) or {}).get("sha256", ""))
        if not _SHA_RE.match(sha):
            problems.append(
                f"{LOCK} has no real sha256 for sidecar/{p} (found {sha!r}); merge "
                "the lock PR release_agent_gaia.yml opened when it published."
            )
    return problems


def check_hub(hub: dict, version: str, lock: dict) -> list[str]:
    """Problems with what the hub serves for the flagship."""
    problems = []
    entry = (hub.get("versions") or {}).get(version)
    if entry is None:
        return [
            f"the Agent Hub has no gaia {version} (published: "
            f"{', '.join(sorted(hub.get('versions') or {})) or 'none'})."
        ]
    if hub.get("latest_version") != version:
        problems.append(
            f"the Agent Hub's latest gaia is {hub.get('latest_version')}, not {version}; "
            "`gaia init` would install that one."
        )
    published = {
        a.get("filename"): a.get("sha256") for a in entry.get("artifacts") or []
    }
    pinned = ((lock.get("components") or {}).get("sidecar") or {}).get(
        "platforms"
    ) or {}
    for p in REQUIRED_PLATFORMS:
        rec = pinned.get(p) or {}
        name = rec.get("filename")
        if name not in published:
            problems.append(f"the Agent Hub's gaia {version} has no {name or p}.")
        elif published[name] != rec.get("sha256"):
            problems.append(
                f"the Agent Hub's {name} for {version} has sha256 {published[name]}, "
                f"but {LOCK} pins {rec.get('sha256')}."
            )
    return problems


def check_built_from(ref: str, version: str) -> list[str]:
    """Problems with the flagship tag's relationship to the release commit."""
    tag = f"{TAG_PREFIX}{version}"
    try:
        _git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
    except subprocess.CalledProcessError:
        return [
            f"tag {tag} does not exist, so flagship {version} was never released "
            "(or this checkout lacks tags: fetch with --tags)."
        ]
    changed = _git("diff", "--name-only", tag, ref, "--", *FROZEN_INPUTS).split()
    if changed:
        shown = "\n    ".join(changed[:20])
        more = f"\n    ... and {len(changed) - 20} more" if len(changed) > 20 else ""
        return [
            f"flagship {version} ({tag}) was frozen from code that differs from "
            f"{ref} in {len(changed)} file(s) the binary is built from:\n    "
            f"{shown}{more}"
        ]
    return []


def fetch_hub_manifest(hub_url: str) -> dict:
    url = f"{hub_url.rstrip('/')}/agents/gaia/manifest.json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(
            req, timeout=60
        ) as r:  # noqa: S310 - fixed https host
            return json.loads(r.read())
    except (urllib.error.URLError, ConnectionError, ValueError, TimeoutError) as e:
        raise SystemExit(f"could not read the Agent Hub manifest at {url}: {e}") from e


def run(
    ref: str, hub_url: str, fetch: Callable[[str], dict] = fetch_hub_manifest
) -> list[str]:
    version = flagship_version(_read_at(ref, MANIFEST))
    lock = json.loads(_read_at(ref, LOCK))
    print(f"flagship at {ref}: {version}")
    problems = check_lock(lock, version)
    problems += check_built_from(ref, version)
    problems += check_hub(fetch(hub_url), version, lock)
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--ref", default="HEAD", help="release commit or tag (default HEAD)"
    )
    ap.add_argument("--hub-url", default=DEFAULT_HUB_URL)
    args = ap.parse_args(argv)

    problems = run(args.ref, args.hub_url)
    if problems:
        for p in problems:
            print(f"::error::{p}", flush=True)
        print(f"::error::{HOW_TO_FIX}", flush=True)
        return 1
    print("flagship is built from this release, pinned and published.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
