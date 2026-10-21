#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Open the PR that commits a flagship release's real hashes to main.

release_agent_gaia.yml regenerates hub/agents/gaia/npm/binaries.lock.json with
the hashes of what it just published, but only the npm tarball carried that
copy. The installer build and the final core-release check read the COMMITTED
lock, so after every flagship release they broke until someone pinned the
hashes by hand (#3932, #5341, #3949).

Nothing pushes to main directly, so the release ends by running this: it
commits the regenerated lock to ``release/flagship-lock-v<version>`` and opens
a PR against main.

Safe to re-run:

* main already carries these hashes -> nothing to do;
* the PR is already open -> its branch is refreshed if it differs, and no
  second PR is opened;
* main's lock is for another flagship version -> refuse, rather than open a PR
  that rolls main back to this release.

Every failure exits non-zero naming what to do; there is no "skip the PR" path.

Usage::

    python util/open_flagship_lock_pr.py --lock regenerated/binaries.lock.json \\
        --version 0.3.1 --repo amd/gaia [--run-url URL]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK = "hub/agents/gaia/npm/binaries.lock.json"
BRANCH_PREFIX = "release/flagship-lock-v"
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
# darwin-x64 is best-effort in the release, so it may be absent.
REQUIRED_SIDECAR = ("win32-x64", "darwin-arm64", "linux-x64")

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]

PERMISSION_MARKERS = (
    "resource not accessible by integration",
    "not permitted to create or approve pull requests",
    "permission to",
    "403",
    "write access",
)


class LockPRError(Exception):
    """The lock PR could not be opened or refreshed; the message says why and what to do."""


def _default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd), cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )


def branch_name(version: str) -> str:
    return f"{BRANCH_PREFIX}{version}"


def lock_problems(lock: dict, version: str) -> list[str]:
    """Why *lock* must not be committed as flagship *version*'s pin."""
    problems = []
    if lock.get("agentVersion") != version:
        problems.append(
            f"the lock's agentVersion is {lock.get('agentVersion')!r}, not {version}."
        )
    sidecar = (lock.get("components") or {}).get("sidecar") or {}
    if sidecar.get("componentVersion") != version:
        problems.append(
            f"the sidecar componentVersion is {sidecar.get('componentVersion')!r}, "
            f"not {version}."
        )
    if (
        not str(sidecar.get("baseUrl", ""))
        .rstrip("/")
        .endswith(f"agents/gaia/{version}")
    ):
        problems.append(f"the sidecar baseUrl does not point at agents/gaia/{version}.")
    for platform in REQUIRED_SIDECAR:
        if platform not in (sidecar.get("platforms") or {}):
            problems.append(f"sidecar/{platform} is missing.")
    for component, lane in sorted((lock.get("components") or {}).items()):
        for platform, rec in sorted((lane.get("platforms") or {}).items()):
            sha = str((rec or {}).get("sha256", ""))
            size = (rec or {}).get("size")
            if not _SHA_RE.match(sha) or not isinstance(size, int) or size <= 0:
                problems.append(
                    f"{component}/{platform} has no real sha256 + size "
                    f"(sha256={sha!r}, size={size!r})."
                )
    return problems


def _is_permission_error(stderr: str) -> bool:
    low = stderr.lower()
    return any(m in low for m in PERMISSION_MARKERS)


def _fail(what: str, proc: subprocess.CompletedProcess, hint: str) -> LockPRError:
    detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
    return LockPRError(f"{what}: {detail}\n{hint}")


def _pr_body(version: str, run_url: str | None) -> str:
    run = f" ([release run]({run_url}))" if run_url else ""
    return (
        f"Flagship agent {version} is published{run}. This commits the checksums of "
        "the binaries it published to `binaries.lock.json` on main, so the desktop "
        "installer build and the final core-release check verify against the build "
        "users actually download. Until it merges, both fail on this version.\n\n"
        "The file is byte-identical to the `binaries.lock.json` inside the published "
        f"`@amd-gaia/gaia@{version}` npm package; the release re-fetched and re-hashed "
        "every entry from the public origin before opening this PR. The release "
        "workflow opened it, so PR checks don't start on their own; the merge queue "
        "runs them.\n\n"
        "## Test plan\n"
        "- [ ] `git fetch --tags origin && python util/check_flagship_release.py "
        f"--ref origin/{branch_name(version)}` passes\n"
    )


def open_lock_pr(
    lock_text: str,
    version: str,
    repo: str,
    *,
    base: str = "main",
    run_url: str | None = None,
    run: Runner = _default_runner,
    workdir: Path = REPO_ROOT,
) -> str:
    """Make sure a PR carrying *lock_text* is open against *base*, or that it already landed.

    Returns a one-line outcome. Raises LockPRError on anything it cannot do.
    """
    try:
        new = json.loads(lock_text)
    except ValueError as e:
        raise LockPRError(f"the regenerated lock is not valid JSON: {e}") from e
    problems = lock_problems(new, version)
    if problems:
        raise LockPRError(
            f"refusing to commit this lock as flagship {version}'s pin:\n  - "
            + "\n  - ".join(problems)
            + "\nThe publish job's regenerate + fetch-verify steps should have caught "
            "this; do not hand-edit the lock to get past it."
        )

    fetch = run(["git", "fetch", "--no-tags", "origin", base])
    if fetch.returncode != 0:
        raise _fail(
            f"could not fetch origin/{base}",
            fetch,
            "Check the checkout step and network access, then re-run this job.",
        )
    shown = run(["git", "show", f"origin/{base}:{LOCK}"])
    if shown.returncode != 0:
        raise _fail(
            f"could not read {LOCK} on origin/{base}",
            shown,
            "The lock must exist on main for the flagship release to pin it.",
        )
    try:
        on_main = json.loads(shown.stdout)
    except ValueError as e:
        raise LockPRError(f"{LOCK} on origin/{base} is not valid JSON: {e}") from e

    if on_main == new:
        return (
            f"{base} already pins flagship {version}'s published hashes; no PR needed."
        )
    if on_main.get("agentVersion") != version:
        raise LockPRError(
            f"{LOCK} on {base} is for flagship {on_main.get('agentVersion')!r}, not "
            f"{version}. Committing {version}'s hashes would roll main back (or "
            f"forward) to a different release. If {version} is the current flagship, "
            f"land its version bump on {base} first and re-run this job; if main has "
            "moved on to a newer flagship, this release's lock no longer belongs there."
        )

    branch = branch_name(version)
    listed = run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--head",
            branch,
            "--base",
            base,
            "--state",
            "open",
            "--json",
            "number,url",
        ]
    )
    if listed.returncode != 0:
        raise _fail(
            f"could not list open PRs for {branch}",
            listed,
            "The job's GH_TOKEN needs read access to pull requests.",
        )
    try:
        open_prs = json.loads(listed.stdout or "[]")
    except ValueError as e:
        raise LockPRError(f"unexpected `gh pr list` output: {listed.stdout!r}") from e

    on_branch = None
    if (
        run(
            [
                "git",
                "fetch",
                "--no-tags",
                "origin",
                f"+{branch}:refs/remotes/origin/{branch}",
            ]
        ).returncode
        == 0
    ):
        prior = run(["git", "show", f"origin/{branch}:{LOCK}"])
        if prior.returncode == 0:
            try:
                on_branch = json.loads(prior.stdout)
            except ValueError:
                on_branch = None  # unreadable counts as stale: rebuild the branch

    if on_branch != new:
        _push_branch(lock_text, version, branch, base, run, workdir)

    if open_prs:
        pr = open_prs[0]
        verb = "refreshed" if on_branch != new else "already carries these hashes"
        return f"PR #{pr['number']} ({pr['url']}) is already open; branch {verb}."

    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8"
    ) as body:
        body.write(_pr_body(version, run_url))
    try:
        created = run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                repo,
                "--base",
                base,
                "--head",
                branch,
                "--title",
                f"chore(release): pin flagship agent {version}'s published hashes",
                "--body-file",
                body.name,
            ]
        )
    finally:
        os.unlink(body.name)
    if created.returncode != 0:
        hint = (
            "The job's token cannot open pull requests. Give the job "
            "`permissions: pull-requests: write`, and turn on Settings -> Actions -> "
            "General -> 'Allow GitHub Actions to create and approve pull requests'."
            if _is_permission_error(created.stderr or "")
            else "Re-run this job; it is idempotent."
        )
        raise _fail(
            f"branch {branch} is pushed but the PR could not be opened",
            created,
            hint + f" To unblock by hand: gh pr create --repo {repo} --base {base} "
            f"--head {branch}",
        )
    return f"opened {created.stdout.strip()}"


def _push_branch(
    lock_text: str,
    version: str,
    branch: str,
    base: str,
    run: Runner,
    workdir: Path,
) -> None:
    steps = [
        ["git", "checkout", "-B", branch, f"origin/{base}"],
        None,  # write the file
        ["git", "add", LOCK],
        [
            "git",
            "-c",
            f"user.name={BOT_NAME}",
            "-c",
            f"user.email={BOT_EMAIL}",
            "commit",
            "-m",
            f"chore(release): pin flagship agent {version}'s published hashes",
            "-m",
            "Commits the binaries.lock.json release_agent_gaia.yml regenerated and "
            "verified when it published this version, so the installer build and "
            "the final core-release check read the hashes users actually download.",
        ],
    ]
    for cmd in steps:
        if cmd is None:
            (workdir / LOCK).write_text(lock_text, encoding="utf-8")
            continue
        proc = run(cmd)
        if proc.returncode != 0:
            raise _fail(f"`{' '.join(cmd[:3])}` failed", proc, "Re-run this job.")

    # Force: the branch is this job's alone, and is rebuilt from main each time.
    pushed = run(["git", "push", "--force", "origin", f"HEAD:refs/heads/{branch}"])
    if pushed.returncode != 0:
        hint = (
            "The job's token cannot push branches. Give the job "
            "`permissions: contents: write`."
            if _is_permission_error(pushed.stderr or "")
            else "Re-run this job; it is idempotent."
        )
        raise _fail(f"could not push {branch}", pushed, hint)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--lock", required=True, type=Path, help="the regenerated lock")
    ap.add_argument("--version", required=True, help="the flagship version published")
    ap.add_argument("--repo", required=True, help="owner/name, e.g. amd/gaia")
    ap.add_argument("--base", default="main")
    ap.add_argument("--run-url", help="the release run, linked from the PR body")
    args = ap.parse_args(argv)

    try:
        outcome = open_lock_pr(
            args.lock.read_text(encoding="utf-8"),
            args.version,
            args.repo,
            base=args.base,
            run_url=args.run_url,
        )
    except (LockPRError, OSError) as e:
        for line in str(e).splitlines():
            print(f"::error::{line}", flush=True)
        print(
            f"::error::The flagship {args.version} is published, but main's lock still "
            "does not pin it; installer builds and the final core release fail until "
            "it does.",
            flush=True,
        )
        return 1
    print(outcome)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"- binaries.lock.json on main: {outcome}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
