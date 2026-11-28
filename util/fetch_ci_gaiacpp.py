#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Get the gaiacpp that main's last green build produced, for a test lane.

A lane that tests the Python side of GAIA against the real native agent needs a
gaiacpp and has no C++ toolchain step of its own. Building one there costs
minutes per run; build_agents.yml already built this platform's binary when
the commit reached main, and keeps it as a run artifact.

    python util/fetch_ci_gaiacpp.py --out "$RUNNER_TEMP/gaiacpp"

prints the path of the binary on stdout (and the run and commit it came from
on stderr). It needs a token in ``GH_TOKEN`` (or ``GITHUB_TOKEN``) that can
read this repository's Actions artifacts (``actions: read``), and nothing but
Python: the self-hosted pool has no ``gh`` or ``curl`` on every host.

For tests only. Nothing that ships takes its gaiacpp from here: installers use
installer/tui/fetch_gaiacpp.py, which names a release and checks its checksums.
A pull request that changes ``cpp/`` is tested against its own gaiacpp by
build_agents.yml, which builds it.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import shutil
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, List, Optional, Tuple

API = "https://api.github.com"
WORKFLOW = "build_agents.yml"
#: build_agents.yml's matrix platforms, by (system, machine).
PLATFORMS = {
    ("Linux", "x86_64"): "linux-x64",
    ("Windows", "AMD64"): "win-x64",
    ("Darwin", "arm64"): "darwin-arm64",
}


class FetchError(RuntimeError):
    """The binary could not be fetched; the message says what to do."""


def this_platform() -> str:
    key = (platform.system(), platform.machine())
    if key not in PLATFORMS:
        raise FetchError(
            f"{WORKFLOW} builds no gaiacpp for {key[0]} {key[1]} "
            f"(it builds {', '.join(sorted(PLATFORMS.values()))}). Pass --platform, "
            "or build gaiacpp here: cmake -S cpp -B cpp/build && "
            "cmake --build cpp/build --target gaiacpp."
        )
    return PLATFORMS[key]


def packaged_name(platform_id: str) -> str:
    """The binary inside the ``cpp-agents-<platform>`` artifact."""
    return f"gaia-{platform_id}" + (".exe" if platform_id.startswith("win-") else "")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _token() -> str:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token.strip():
        raise FetchError(
            "no GitHub token: set GH_TOKEN to one that can read this repository's "
            "Actions artifacts (in a workflow: GH_TOKEN: ${{ github.token }} with "
            "`permissions: actions: read`)."
        )
    return token.strip()


def _get(url: str, token: str, follow: bool = True) -> Any:
    """GET *url* with the token; the open response."""
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "gaia-ci-fetch",
        },
    )
    opener = (
        urllib.request.build_opener()
        if follow
        else urllib.request.build_opener(_NoRedirect)
    )
    try:
        return opener.open(request, timeout=120)
    except urllib.error.HTTPError as e:
        if not follow and e.code in (301, 302, 303, 307, 308):
            return e
        detail = e.read().decode("utf-8", "replace")[:300]
        raise FetchError(
            f"GET {url} answered HTTP {e.code}: {detail}. The token needs "
            "actions: read on this repository."
        ) from e
    except urllib.error.URLError as e:
        raise FetchError(f"GET {url} failed: {e.reason}") from e
    except ConnectionError as e:
        raise FetchError(f"GET {url} failed: the connection dropped ({e})") from e


def _json(url: str, token: str) -> dict:
    with _get(url, token) as reply:
        return json.load(reply)


def green_runs(repo: str, branch: str, token: str, count: int = 10) -> List[dict]:
    """The last *count* successful push runs on *branch*, newest first."""
    query = urllib.parse.urlencode(
        {"branch": branch, "event": "push", "status": "success", "per_page": count}
    )
    runs = _json(
        f"{API}/repos/{repo}/actions/workflows/{WORKFLOW}/runs?{query}", token
    ).get("workflow_runs", [])
    if not runs:
        raise FetchError(
            f"{WORKFLOW} has no successful push run on {branch} in {repo}, so there "
            f"is no gaiacpp to fetch. Dispatch {WORKFLOW} on {branch} and run this "
            "again."
        )
    return runs


def _live_artifact(repo: str, run: dict, name: str, token: str) -> Optional[dict]:
    listing = _json(
        f"{API}/repos/{repo}/actions/runs/{run['id']}/artifacts?"
        + urllib.parse.urlencode({"name": name}),
        token,
    )
    live = [a for a in listing.get("artifacts", []) if not a.get("expired")]
    return live[0] if live else None


def newest_with_artifact(
    repo: str, branch: str, name: str, token: str
) -> Tuple[dict, dict]:
    """The newest green run that still keeps *name*, and that artifact."""
    runs = green_runs(repo, branch, token)
    for run in runs:
        artifact = _live_artifact(repo, run, name, token)
        if artifact:
            return run, artifact
    raise FetchError(
        f"none of the last {len(runs)} green {WORKFLOW} runs on {branch} keeps a "
        f"{name} artifact (they expire). Dispatch {WORKFLOW} on {branch} and run "
        "this again."
    )


def _artifact_zip(artifact: dict, name: str, token: str) -> bytes:
    # The download is a redirect to storage that refuses GitHub's token.
    redirect = _get(artifact["archive_download_url"], token, follow=False)
    location = redirect.headers.get("Location")
    if not location:
        raise FetchError(
            f"GitHub did not say where {name} is stored (HTTP {redirect.status})."
        )
    try:
        with urllib.request.urlopen(location, timeout=300) as reply:
            return reply.read()
    except (urllib.error.URLError, ConnectionError) as e:
        raise FetchError(f"downloading {name} failed: {e}") from e


def fetch(out: Path, platform_id: str, repo: str, branch: str = "main") -> Path:
    token = _token()
    artifact = f"cpp-agents-{platform_id}"
    run, found = newest_with_artifact(repo, branch, artifact, token)
    member = f"gaia/{packaged_name(platform_id)}"
    with zipfile.ZipFile(io.BytesIO(_artifact_zip(found, artifact, token))) as zf:
        if member not in zf.namelist():
            raise FetchError(
                f"run {run['id']}'s {artifact} artifact has no {member}; it holds "
                f"{sorted(zf.namelist())}."
            )
        binary = out / ("gaiacpp.exe" if platform_id.startswith("win-") else "gaiacpp")
        with zf.open(member) as packed, open(binary, "wb") as unpacked:
            shutil.copyfileobj(packed, unpacked)
    # An artifact is a zip: it does not keep the executable bit.
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(
        f"gaiacpp from {WORKFLOW} run {run['id']} "
        f"({branch} at {run['head_sha'][:12]}, {run['created_at']})",
        file=sys.stderr,
    )
    return binary.resolve()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", required=True, type=Path, help="folder to put it in")
    ap.add_argument("--platform", choices=sorted(PLATFORMS.values()))
    ap.add_argument("--branch", default="main")
    ap.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY") or "amd/gaia",
        help="owner/name (default: GITHUB_REPOSITORY, else amd/gaia)",
    )
    args = ap.parse_args(argv)
    try:
        args.out.mkdir(parents=True, exist_ok=True)
        binary = fetch(
            args.out, args.platform or this_platform(), args.repo, args.branch
        )
    except FetchError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(binary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
