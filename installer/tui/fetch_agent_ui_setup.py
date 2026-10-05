#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fetch the desktop app setup the Windows GAIA setup embeds as a component.

The one Windows setup (installer/tui/nsis/gaia-setup.nsi) does not rebuild the
Electron app: it embeds the exact gaia-agent-ui-<version>-x64-setup.exe that
build-installers.yml produced, because that file is also what electron-updater
re-runs. This finds that file from one of two named sources -- never a guessed
"latest":

``--release vX.Y.Z``
    The asset on a published GitHub release. For manual runs and the PR gate.

``--publish-run-sha SHA --version X.Y.Z``
    The ``windows-installer`` artifact of publish.yml's run for tag vX.Y.Z at
    that commit, taken only once the run's ``Build complete`` job has passed.
    Those jobs run before publish.yml's approval gate, so a release does not
    wait on a human to build the Windows setup. Polled up to ``--wait-minutes``.

Either way the file must be named for the expected version, be a PE image, and
be at least 50 MiB -- the same floor build-installers.yml enforces. Anything
else exits non-zero with what to do next.

Needs the ``gh`` CLI with GH_TOKEN set (``actions: read`` for the run source).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

PUBLISH_WORKFLOW = "publish.yml"
RUN_ARTIFACT = "windows-installer"
# build-installers.yml uploads the artifact mid-build, before its validation and
# smoke jobs; this last job passing is what says the setup is good to ship.
GATE_JOB = "Build complete"
MIN_BYTES = 50 * 1024 * 1024
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def setup_name(version: str) -> str:
    return f"gaia-agent-ui-{version}-x64-setup.exe"


def _gh(args: list[str]) -> bytes:
    proc = subprocess.run(["gh", *args], capture_output=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(
            f"gh {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.decode(errors='replace').strip()}"
        )
    return proc.stdout


def _api(path: str) -> dict:
    return json.loads(_gh(["api", "-H", "Accept: application/vnd.github+json", path]))


def verify(path: Path, version: str) -> Path:
    if path.name != setup_name(version):
        raise SystemExit(f"expected {setup_name(version)}, got {path.name}")
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise SystemExit(f"{path} is not a Windows executable")
    if len(data) < MIN_BYTES:
        raise SystemExit(
            f"{path} is {len(data)} bytes, under the {MIN_BYTES}-byte floor a "
            "desktop app setup clears -- likely a truncated download or an error body."
        )
    return path


def from_release(repo: str, tag: str, out: Path) -> Path:
    version = tag[1:] if tag.startswith("v") else tag
    if not VERSION_RE.match(version):
        raise SystemExit(f"--release '{tag}' is not a vX.Y.Z tag")
    out.mkdir(parents=True, exist_ok=True)
    _gh(
        [
            "release",
            "download",
            tag,
            "--repo",
            repo,
            "--pattern",
            setup_name(version),
            "--dir",
            str(out),
            "--clobber",
        ]
    )
    return verify(out / setup_name(version), version)


def _publish_run(repo: str, sha: str, tag: str) -> dict | None:
    # By tag as well as commit: an rc tag and the final tag can share a commit.
    runs = _api(
        f"repos/{repo}/actions/workflows/{PUBLISH_WORKFLOW}/runs"
        f"?head_sha={sha}&event=push&per_page=20"
    ).get("workflow_runs", [])
    runs = [r for r in runs if r.get("head_branch") == tag]
    return max(runs, key=lambda r: r["run_number"]) if runs else None


def _gate_job(repo: str, run_id: int) -> dict | None:
    jobs = _api(
        f"repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100"
    ).get("jobs", [])
    for job in jobs:
        if job["name"] == GATE_JOB or job["name"].endswith(f"/ {GATE_JOB}"):
            return job
    return None


def _run_artifacts(repo: str, run_id: int) -> list[dict]:
    return _api(
        f"repos/{repo}/actions/runs/{run_id}/artifacts?name={RUN_ARTIFACT}"
    ).get("artifacts", [])


def from_publish_run(
    repo: str,
    sha: str,
    version: str,
    out: Path,
    wait_minutes: int,
    poll_seconds: int = 60,
) -> Path:
    if not VERSION_RE.match(version):
        raise SystemExit(f"--version '{version}' is not X.Y.Z")
    tag = f"v{version}"
    deadline = time.monotonic() + wait_minutes * 60
    while True:
        run = _publish_run(repo, sha, tag)
        if run is None:
            state = f"no {PUBLISH_WORKFLOW} push run for {tag} at {sha} yet"
        else:
            gate = _gate_job(repo, run["id"])
            if gate is not None and gate["status"] == "completed":
                if gate["conclusion"] != "success":
                    raise SystemExit(
                        f"'{gate['name']}' in {run['html_url']} ended "
                        f"{gate['conclusion']}: the desktop app setup failed its own "
                        "checks, so it is not embedded. Fix that run, then re-run this one."
                    )
                arts = _run_artifacts(repo, run["id"])
                live = [a for a in arts if not a.get("expired")]
                if live:
                    art = live[0]
                    break
                # build-installers.yml keeps it 14 days; past that it is listed as
                # expired or not listed at all.
                raise SystemExit(
                    f"{run['html_url']} has no live '{RUN_ARTIFACT}' artifact -- it is "
                    "past its 14-day retention. Take the setup from the published "
                    f"release instead: --release {tag}"
                )
            if run["status"] == "completed":
                raise SystemExit(
                    f"{PUBLISH_WORKFLOW} run {run['html_url']} finished "
                    f"({run['conclusion']}) without its '{GATE_JOB}' job completing, so "
                    "there is no checked desktop app setup to embed. Fix that run's "
                    "build-desktop-installers job, then re-run this one."
                )
            state = f"run {run['id']} is {run['status']}; waiting for '{GATE_JOB}'"
        if time.monotonic() >= deadline:
            raise SystemExit(
                f"timed out after {wait_minutes} min waiting for the desktop app "
                f"setup ({state}). Check {PUBLISH_WORKFLOW} for this tag, then "
                "re-run this workflow."
            )
        print(
            f"[fetch_agent_ui_setup] {state}; retrying in {poll_seconds}s", flush=True
        )
        time.sleep(poll_seconds)

    archive = _gh(["api", f"repos/{repo}/actions/artifacts/{art['id']}/zip"])
    name = setup_name(version)
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        if name not in zf.namelist():
            raise SystemExit(
                f"artifact '{RUN_ARTIFACT}' of run {run['id']} has no {name} "
                f"(it holds: {', '.join(zf.namelist())}). The release version and "
                "src/gaia/apps/webui/package.json disagree."
            )
        out.mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(zf.read(name))
    return verify(out / name, version)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--release", metavar="TAG", help="a published vX.Y.Z release")
    src.add_argument(
        "--publish-run-sha",
        metavar="SHA",
        help="publish.yml's push run for this commit",
    )
    ap.add_argument(
        "--version", help="expected X.Y.Z (required with --publish-run-sha)"
    )
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "amd/gaia"))
    ap.add_argument("--wait-minutes", type=int, default=60)
    args = ap.parse_args(argv)

    if args.release:
        path = from_release(args.repo, args.release, args.out)
    else:
        if not args.version:
            ap.error("--publish-run-sha needs --version")
        path = from_publish_run(
            args.repo, args.publish_run_sha, args.version, args.out, args.wait_minutes
        )
    print(f"[fetch_agent_ui_setup] {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
