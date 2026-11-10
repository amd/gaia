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

The desktop app's update manifests (latest*.yml) are read too: every file one
names must be attached at the size it records. Through v0.25.4-rc4,
latest-mac.yml named a .zip no release carried, so no Mac could update (#5682).

Usage::

    python util/check_release_assets.py --repo amd/gaia --tag v0.25.3
    # Before the release exists: the manifests in a directory against its files
    python util/check_release_assets.py --dir release-assets
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

import yaml

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
# The frozen gaia install.sh and install.ps1 fetch. The best-effort Intel Mac
# build is not promised: a release without it only leaves that Mac with no
# one-line install.
CORE_FILES = (
    "gaia-core-win32-x64.tar.gz",
    "gaia-core-darwin-arm64.tar.gz",
    "gaia-core-linux-x64.tar.gz",
    "gaia-core-SHA256SUMS.txt",
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
        f"{ui}-arm64.zip",
        f"{ui}-arm64.zip.blockmap",
        f"{ui}-amd64.deb",
        f"{ui}-x86_64.AppImage",
        *UPDATE_FEEDS,
        *TUI_FILES,
        *CORE_FILES,
        INSTALLER_SUMS,
    ]
    for platform in sorted(artifacts.ARTIFACTS):
        if platform == WINDOWS_SETUP_PLATFORM and not windows_setup:
            continue
        if rel.is_rc or artifacts.platform_has_sidecar(platform):
            names += [n for n, _ in artifacts.artifacts_for(platform, rel.npm_version)]
    return names


def _ready(assets: list[dict]) -> dict[str, int]:
    """Name -> size of every asset that finished uploading with content."""
    return {
        a["name"]: a["size"]
        for a in assets
        if a.get("state") == "uploaded" and a.get("size", 0) > 0
    }


def missing_assets(promised: list[str], assets: list[dict]) -> list[str]:
    """Promised names the release lacks, or holds as a failed or empty upload."""
    ready = _ready(assets)
    return [name for name in promised if name not in ready]


def manifest_problems(
    manifests: dict[str, str],
    sizes: dict[str, int],
    sha512: Callable[[str], str] | None = None,
) -> list[str]:
    """Why each update manifest would fail the desktop app's updater, if it would.

    ``manifests`` maps a latest*.yml name to its text; ``sizes`` holds every
    attached file. With ``sha512`` (name -> base64 digest) digests are compared
    too, which catches a file rewritten after the manifest recorded it.
    """
    problems = []
    for feed, text in sorted(manifests.items()):
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            problems.append(f"{feed} is not valid YAML ({e})")
            continue
        files = data.get("files") if isinstance(data, dict) else None
        if (
            not isinstance(files, list)
            or not files
            or not isinstance(data.get("path"), str)
        ):
            problems.append(f"{feed} has no files list or no path")
            continue
        entries: dict[str, dict] = {}
        for entry in files:
            if not isinstance(entry, dict) or not isinstance(entry.get("url"), str):
                problems.append(f"{feed} has a files entry with no url: {entry!r}")
                continue
            entries[entry["url"]] = entry
        path = data["path"]
        named = list(entries)
        if path not in entries:
            named.append(path)
        # The updater fetches the installer's blockmap for a delta download
        # unless the manifest says it is embedded (blockMapSize: the AppImage).
        if "blockMapSize" not in entries.get(path, {}):
            named.append(f"{path}.blockmap")
        for name in named:
            if name not in sizes:
                problems.append(f"{feed} names {name}, which is not attached")
                continue
            entry = entries.get(name, {})
            if "size" in entry and entry["size"] != sizes[name]:
                problems.append(
                    f"{feed} records {name} at {entry['size']} bytes, "
                    f"but the attached file is {sizes[name]}"
                )
            if sha512 and "sha512" in entry and entry["sha512"] != sha512(name):
                problems.append(
                    f"{feed} records a sha512 for {name} that the attached file "
                    "does not match (was it changed, e.g. signed, after the build?)"
                )
    return problems


def _sha512(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return base64.b64encode(digest.digest()).decode()


def check_dir(directory: Path) -> list[str]:
    """Manifest problems for the latest*.yml in ``directory`` against its files."""
    if not directory.is_dir():
        return [f"{directory} is not a directory"]
    manifests = {
        p.name: p.read_text(encoding="utf-8") for p in directory.glob("latest*.yml")
    }
    if not manifests:
        return [f"{directory} holds no latest*.yml update manifest"]
    sizes = {p.name: p.stat().st_size for p in directory.iterdir() if p.is_file()}
    return manifest_problems(manifests, sizes, lambda n: _sha512(directory / n))


GH_TIMEOUT = 120


def fetch_manifests(repo: str, tag: str, feeds: list[str]) -> dict[str, str]:
    if not feeds:
        return {}
    with tempfile.TemporaryDirectory() as tmp:
        cmd = ["gh", "release", "download", tag, "--repo", repo, "--dir", tmp]
        for feed in feeds:
            cmd += ["--pattern", feed]
        try:
            done = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=GH_TIMEOUT,
            )
        except subprocess.TimeoutExpired as e:
            raise SystemExit(
                f"::error::downloading {' '.join(feeds)} from release {tag} of "
                f"{repo} did not finish within {GH_TIMEOUT}s. GitHub "
                "may be slow or down: re-run this job."
            ) from e
        if done.returncode != 0:
            raise SystemExit(
                f"::error::could not download {' '.join(feeds)} from release {tag} "
                f"of {repo}: {done.stderr.strip()}. A rate limit clears within the "
                "hour: re-run this job then."
            )
        return {feed: (Path(tmp) / feed).read_text(encoding="utf-8") for feed in feeds}


def fetch_assets(repo: str, tag: str) -> list[dict]:
    try:
        done = subprocess.run(
            ["gh", "release", "view", tag, "--repo", repo, "--json", "assets"],
            capture_output=True,
            text=True,
            check=False,
            timeout=GH_TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        raise SystemExit(
            f"::error::reading release {tag} of {repo} did not finish within "
            f"{GH_TIMEOUT}s. GitHub may be slow or down: re-run this job."
        ) from e
    if done.returncode != 0:
        raise SystemExit(
            f"::error::could not read release {tag} of {repo}: {done.stderr.strip()}. "
            "A rate limit clears within the hour: re-run this job then. Otherwise "
            "check that publish.yml's 'GitHub Release' job created the release."
        )
    return json.loads(done.stdout)["assets"]


def _main_dir(directory: Path) -> int:
    problems = check_dir(directory)
    if problems:
        print(
            f"::error::the desktop app's update manifests in {directory} would break "
            f"its updater: {'; '.join(problems)}. Each latest*.yml must ship with "
            "every file it names, as built: check that the platform's installer "
            "artifact collects them (the build matrix in build-installers.yml)."
        )
        return 1
    print(f"OK: every file the update manifests in {directory} name is there, as built")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", help="owner/name")
    ap.add_argument("--tag", help="vX.Y.Z or vX.Y.Z-rcN")
    ap.add_argument(
        "--skip-windows-setup",
        action="store_true",
        help="an RC only: its notes say the setup failed to build, so none is promised",
    )
    ap.add_argument(
        "--dir",
        type=Path,
        help="check only the update manifests in this directory against its files",
    )
    args = ap.parse_args(argv)

    if args.dir is not None:
        if args.repo or args.tag or args.skip_windows_setup:
            ap.error("--dir takes no --repo, --tag or --skip-windows-setup")
        return _main_dir(args.dir)
    if not (args.repo and args.tag):
        ap.error("give --repo and --tag, or --dir")

    rel = parse_tag(args.tag)
    if args.skip_windows_setup and not rel.is_rc:
        raise SystemExit(
            f"::error::--skip-windows-setup is for release candidates; {args.tag} is a "
            "final, whose notes promise the Windows setup unconditionally."
        )

    promised = promised_assets(rel, windows_setup=not args.skip_windows_setup)
    assets = fetch_assets(args.repo, args.tag)
    missing = missing_assets(promised, assets)
    ready = _ready(assets)
    feeds = [f for f in UPDATE_FEEDS if f in ready]
    problems = manifest_problems(fetch_manifests(args.repo, args.tag, feeds), ready)
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
    if problems:
        print(
            f"::error::release {args.tag}'s desktop update manifests name files it "
            f"does not carry as built, so the desktop app cannot update from it: "
            f"{'; '.join(problems)}. Re-run 'GitHub Release' to attach them from "
            "this run's installer artifacts, or fix the artifact that dropped them "
            "(the build matrix in build-installers.yml)."
        )
    if missing or problems:
        return 1
    print(
        f"OK: release {args.tag} carries all {len(promised)} promised files and "
        f"every file its {len(feeds)} update manifests name"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
