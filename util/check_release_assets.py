#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fail a release that does not carry every installer it promises.

v0.25.3 went out with a green publish run and no combined Windows setup, .pkg,
.deb or .rpm: the job that should have attached them failed in another workflow,
and nothing looked at the release itself. So this reads the published release
through the API, not any job's result, and names each promised file it lacks.

The promise is what docs/guides/install.mdx, the release notes and the website
name. A final's terminal packages follow its flagship lock, the rule
publish.yml's tui-installers-complete applies; an RC needs all of them.

Usage::

    python util/check_release_assets.py --repo amd/gaia --tag v0.25.3
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "installer" / "tui"))
sys.path.insert(0, str(REPO_ROOT / "util"))

import artifacts  # noqa: E402
from release_tag import ReleaseTag, parse_tag  # noqa: E402

WINDOWS_SETUP_PLATFORM = "win-x64"
INSTALLER_SUMS = "gaia-installers-SHA256SUMS.txt"
TUI_FILES = (
    "gaia-linux-amd64",
    "gaia-linux-arm64",
    "gaia-darwin-amd64",
    "gaia-darwin-arm64",
    "gaia-windows-amd64.exe",
    "gaia-windows-arm64.exe",
    "gaia-tui-SHA256SUMS.txt",
)
UPDATE_FEEDS = ("latest.yml", "latest-mac.yml", "latest-linux.yml")


def promised_assets(rel: ReleaseTag, windows_setup: bool = True) -> list[str]:
    """Every installer-related file this release must carry."""
    ui = f"gaia-agent-ui-{rel.npm_version}"
    names = [
        f"{ui}-x64-setup.exe",
        f"{ui}-x64-setup.exe.blockmap",
        f"{ui}-arm64.dmg",
        f"{ui}-arm64.dmg.blockmap",
        f"{ui}-amd64.deb",
        f"{ui}-x86_64.AppImage",
        *UPDATE_FEEDS,
        *TUI_FILES,
        INSTALLER_SUMS,
    ]
    for platform in sorted(artifacts.ARTIFACTS):
        if platform == WINDOWS_SETUP_PLATFORM and not windows_setup:
            continue
        if rel.is_rc or artifacts.platform_has_sidecar(platform):
            names += [n for n, _ in artifacts.artifacts_for(platform, rel.npm_version)]
    return names


def missing_assets(promised: list[str], assets: list[dict]) -> list[str]:
    """Promised names the release lacks, or holds as a failed or empty upload."""
    ready = {
        a["name"]
        for a in assets
        if a.get("state") == "uploaded" and a.get("size", 0) > 0
    }
    return [name for name in promised if name not in ready]


def fetch_assets(repo: str, tag: str) -> list[dict]:
    done = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo, "--json", "assets"],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise SystemExit(
            f"::error::could not read release {tag} of {repo}: {done.stderr.strip()}. "
            "A rate limit clears within the hour: re-run this job then. Otherwise "
            "check that publish.yml's 'GitHub Release' job created the release."
        )
    return json.loads(done.stdout)["assets"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--tag", required=True, help="vX.Y.Z or vX.Y.Z-rcN")
    ap.add_argument(
        "--skip-windows-setup",
        action="store_true",
        help="an RC only: its notes say the setup failed to build, so none is promised",
    )
    args = ap.parse_args(argv)

    rel = parse_tag(args.tag)
    if args.skip_windows_setup and not rel.is_rc:
        raise SystemExit(
            f"::error::--skip-windows-setup is for release candidates; {args.tag} is a "
            "final, whose notes promise the Windows setup unconditionally."
        )

    promised = promised_assets(rel, windows_setup=not args.skip_windows_setup)
    missing = missing_assets(promised, fetch_assets(args.repo, args.tag))
    if missing:
        print(
            f"::error::release {args.tag} is missing {len(missing)} of {len(promised)} "
            f"promised files: {' '.join(missing)}. The install guide, website and "
            "release notes send users to these. Re-run the job that attaches each "
            "one, together with this check, while this run's artifacts last (7 "
            "days): 'GitHub Release' for the desktop app, the Windows setup and the "
            "terminal binaries; 'Attach terminal installers' for the .pkg, .deb, "
            ".rpm and their checksums."
        )
        return 1
    print(f"OK: release {args.tag} carries all {len(promised)} promised files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
