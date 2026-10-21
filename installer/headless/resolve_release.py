#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Pick the GitHub release the installer journeys test, wait for its assets,
and say which installers it carries.

    python resolve_release.py --tag v0.25.1-rc2 --wait-minutes 0 --out release.json
    python resolve_release.py --tag "" ...   # newest pre-release, or a newer final

Writes ``release.json`` and, under Actions, the same keys to $GITHUB_OUTPUT.

An empty --tag picks the newest pre-release -- unless a final release is newer
than it, in which case the final: once v0.25.1 ships, v0.25.1-rc2 is history.

Asset names come from the same places the release pipeline uses
(installer/tui/artifacts.py for the terminal packages; electron-builder's
artifactName for the desktop app), spelled with the release's asset version:
tag v0.25.1-rc2 -> 0.25.1-rc.2.

Waiting exists for the tag trigger: the tag is pushed before publish.yml and
release_components.yml have attached anything, and the approval gate between
them is human. A release still missing assets when the wait ends fails with
the list of what is missing -- testing a half-attached release would report
installers as broken that simply are not there yet.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

API = "https://api.github.com/repos/amd/gaia"
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?$")


def parse_tag(tag: str) -> Tuple[int, int, int, Optional[int]]:
    m = TAG_RE.match(tag)
    if not m:
        raise ValueError(f"not a release tag: {tag!r} (expected vX.Y.Z or vX.Y.Z-rcN)")
    rc = int(m.group(4)) if m.group(4) else None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), rc


def sort_key(tag: str) -> Tuple[int, int, int, int]:
    major, minor, patch, rc = parse_tag(tag)
    # A final sorts after every candidate for it.
    return major, minor, patch, rc if rc is not None else 1 << 30


def asset_version(tag: str) -> str:
    """v0.25.1-rc2 -> 0.25.1-rc.2 (npm/electron-builder form); v0.25.1 -> 0.25.1."""
    major, minor, patch, rc = parse_tag(tag)
    base = f"{major}.{minor}.{patch}"
    return f"{base}-rc.{rc}" if rc is not None else base


def pep440(tag: str) -> str:
    major, minor, patch, rc = parse_tag(tag)
    base = f"{major}.{minor}.{patch}"
    return f"{base}rc{rc}" if rc is not None else base


def core_version(tag: str) -> str:
    major, minor, patch, _ = parse_tag(tag)
    return f"{major}.{minor}.{patch}"


def expected_assets(tag: str) -> Dict[str, str]:
    """Installer id -> the asset name the release should carry for it."""
    v = asset_version(tag)
    names = {
        "win-setup": f"gaia-{v}-win-x64-setup.exe",
        "agent-ui-exe": f"gaia-agent-ui-{v}-x64-setup.exe",
        "agent-ui-dmg": f"gaia-agent-ui-{v}-arm64.dmg",
        "agent-ui-appimage": f"gaia-agent-ui-{v}-x86_64.AppImage",
        "agent-ui-deb": f"gaia-agent-ui-{v}-amd64.deb",
        "tui-bin-linux-x64": "gaia-linux-amd64",
        "tui-bin-darwin-arm64": "gaia-darwin-arm64",
        "tui-bin-darwin-x64": "gaia-darwin-amd64",
        "tui-bin-win-x64": "gaia-windows-amd64.exe",
        "wheel": f"amd_gaia-{pep440(tag)}-py3-none-any.whl",
        # Attached to release candidates (their own freeze); a final takes
        # the published flagship from the hub instead.
        "sidecar-linux-x64": "gaia-agent-linux-x64",
        "sidecar-darwin-arm64": "gaia-agent-darwin-arm64",
        "sidecar-darwin-x64": "gaia-agent-darwin-x64",
        # installer/tui/artifacts.py's names. Built for finals only: an rpm
        # Version cannot carry -rc.N, so a candidate publishes none.
        "tui-pkg-darwin-arm64": f"gaia-{v}-darwin-arm64.pkg",
        "tui-pkg-darwin-x64": f"gaia-{v}-darwin-x64.pkg",
        "tui-deb": f"gaia_{v}_amd64.deb",
        "tui-rpm": f"gaia-{v}.x86_64.rpm",
    }
    return names


def required(tag: str) -> List[str]:
    """What a release of this kind must carry before the journeys can run."""
    _, _, _, rc = parse_tag(tag)
    ids = [
        "win-setup",
        "agent-ui-exe",
        "agent-ui-dmg",
        "agent-ui-appimage",
        "agent-ui-deb",
        "tui-bin-linux-x64",
        "tui-bin-darwin-arm64",
        "tui-bin-darwin-x64",
        "tui-bin-win-x64",
    ]
    if rc is None:
        ids += ["tui-pkg-darwin-arm64", "tui-pkg-darwin-x64", "tui-deb", "tui-rpm"]
    else:
        ids += ["sidecar-linux-x64", "sidecar-darwin-arm64", "sidecar-darwin-x64"]
    return ids


def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def list_releases() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for page in range(1, 4):
        batch = _get(f"{API}/releases?per_page=100&page={page}")
        out += batch
        if len(batch) < 100:
            break
    return [
        r for r in out if not r.get("draft") and TAG_RE.match(r.get("tag_name", ""))
    ]


def pick_default(releases: List[Dict[str, Any]]) -> str:
    pre = [r["tag_name"] for r in releases if r.get("prerelease")]
    final = [r["tag_name"] for r in releases if not r.get("prerelease")]
    if not pre and not final:
        raise SystemExit("error: amd/gaia has no published release to test.")
    newest_pre = max(pre, key=sort_key) if pre else None
    newest_final = max(final, key=sort_key) if final else None
    if newest_pre and (
        not newest_final or sort_key(newest_pre) > sort_key(newest_final)
    ):
        return newest_pre
    return newest_final  # type: ignore[return-value]


def previous_stable(
    releases: List[Dict[str, Any]], tag: str
) -> Optional[Dict[str, Any]]:
    """The newest final release older than ``tag``'s version."""
    major, minor, patch, _ = parse_tag(tag)
    older = [
        r
        for r in releases
        if not r.get("prerelease")
        and sort_key(r["tag_name"])[:3] < (major, minor, patch)
    ]
    return max(older, key=lambda r: sort_key(r["tag_name"])) if older else None


def release_assets(tag: str) -> Optional[Dict[str, str]]:
    """Asset name -> download URL, or None when the release does not exist yet."""
    try:
        rel = _get(f"{API}/releases/tags/{tag}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--tag", default="", help="vX.Y.Z or vX.Y.Z-rcN; empty picks one")
    ap.add_argument("--wait-minutes", type=float, default=0)
    ap.add_argument("--poll-seconds", type=float, default=60)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    releases = list_releases()
    tag = args.tag.strip() or pick_default(releases)
    parse_tag(tag)

    deadline = time.monotonic() + args.wait_minutes * 60
    names = expected_assets(tag)
    while True:
        assets = release_assets(tag)
        missing = [
            names[i] for i in required(tag) if assets is None or names[i] not in assets
        ]
        if not missing:
            break
        if time.monotonic() >= deadline:
            where = (
                "has no GitHub release yet" if assets is None else "is missing assets"
            )
            listing = "\n".join(f"    {m}" for m in missing)
            print(
                f"::error::{tag} {where} after waiting {args.wait_minutes:g} min. Missing:\n{listing}\n"
                f"Check the publish.yml and release_components.yml runs for {tag}; "
                f"re-run this workflow once they have attached everything. Nothing was tested.",
                flush=True,
            )
            return 1
        print(
            f"{tag}: waiting for {len(missing)} asset(s), e.g. {missing[0]}", flush=True
        )
        time.sleep(args.poll_seconds)

    assert assets is not None
    prev = previous_stable(releases, tag)
    prev_tag = prev["tag_name"] if prev else ""
    prev_assets = {
        a["name"]: a["browser_download_url"] for a in (prev or {}).get("assets", [])
    }
    prev_names = expected_assets(prev_tag) if prev_tag else {}

    info: Dict[str, Any] = {
        "tag": tag,
        "version": asset_version(tag),
        "pep440": pep440(tag),
        "core_version": core_version(tag),
        "prerelease": parse_tag(tag)[3] is not None,
        "assets": {i: n for i, n in names.items() if n in assets},
        "missing": {i: n for i, n in names.items() if n not in assets},
        "urls": {n: assets[n] for n in names.values() if n in assets},
        "prev_tag": prev_tag,
        "prev_version": asset_version(prev_tag) if prev_tag else "",
        "prev_pep440": pep440(prev_tag) if prev_tag else "",
        "prev_assets": {i: n for i, n in prev_names.items() if n in prev_assets},
        "prev_urls": {
            n: prev_assets[n] for n in prev_names.values() if n in prev_assets
        },
    }
    Path(args.out).write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            for key in (
                "tag",
                "version",
                "pep440",
                "core_version",
                "prev_tag",
                "prev_version",
                "prev_pep440",
            ):
                fh.write(f"{key}={info[key]}\n")
            fh.write(f"prerelease={'true' if info['prerelease'] else 'false'}\n")
            fh.write(
                f"tui_packages_published={'true' if 'tui-deb' in info['assets'] else 'false'}\n"
            )
    print(
        json.dumps(
            {k: info[k] for k in ("tag", "version", "prev_tag", "missing")}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
