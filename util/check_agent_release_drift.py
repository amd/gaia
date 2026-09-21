# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Fail when a hub agent's merged changes have sat unpublished too long (#3895).

An agent under ``hub/agents/<id>/`` ships to users as a packaged artifact cut by
``release_agent_<id>.yml`` from a ``agent-pkg-<id>-<version>`` tag. Nothing in
the PR loop observes that artifact: a fix goes green against repo source, merges,
and looks shipped while the thing users install never moves. The email agent sat
19 days and two milestone-blocking fixes behind its published build that way.

This check is the missing observer. For each agent that has ever been released it
resolves the newest release tag, lists the commits touching that agent's package
since, and fails when the OLDEST of them is older than ``--max-age-days``.

Deliberately measured from the oldest unreleased commit, not the newest: the
question is "how long has finished work been unreachable", and a steady trickle
of new commits must never reset that clock.

**Why this is scheduled rather than run at release time.** The failure being
caught is that *no release ran at all*. A check wired into the release workflow
observes nothing while the lane is idle — it can only speak when the very thing
whose absence is the bug happens to be running. So this is built to run on a
timer against ``main``, where an idle lane is exactly what it can see.

Two properties are load-bearing, both learned from real tags in this repo:

- **The tag glob is ``agent-pkg-<id>-*``, and a leading ``v`` is optional.**
  ``release_agent_email.yml`` triggers on ``agent-pkg-email-*`` and strips an
  optional ``v`` when parsing the version, so both spellings are real releases.
  The 0.6.0 release was in fact tagged ``agent-pkg-email-0.6.0`` with no ``v``.
  A checker globbing ``agent-pkg-<id>-v*`` would not see it and would report
  ~5 weeks of drift instead of ~2 — a false positive of the kind that gets a
  gate ignored.
- **Versions sort by semver, never lexically.** Lexically ``0.9.0`` beats
  ``0.10.0``, which would silently pick an older release as "latest" and hide
  real drift.

**Known blind spot, stated on purpose:** this reads git only — no network — so it
runs offline in any clone and is testable without touching the Hub. That means it
cannot see an artifact published with no tag behind it at all. Its git-visible
shadow *is* reported: when the manifest version differs from the newest tag's,
the finding says so, since a bump that never became a tag is the usual way that
state arises.

Run via ``python util/check_agent_release_drift.py`` or the scheduled
``agent_release_drift.yml`` workflow. Needs full tag history — a default shallow
Actions checkout has none (``fetch-depth: 0``).
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / "hub" / "agents"

# Default SLA. The issue that prompted this check was filed at 19 days; a week
# is short enough to catch a stalled lane while leaving room for the normal
# merge-bump-then-tag window, which closes in hours.
DEFAULT_MAX_AGE_DAYS = 7

_TAG_PREFIX = "agent-pkg-"
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


class Commit(NamedTuple):
    sha: str
    when: datetime
    subject: str


class Finding(NamedTuple):
    agent_id: str
    status: str  # "ok" | "never-released" | "pending" | "drift"
    message: str

    @property
    def is_error(self) -> bool:
        return self.status == "drift"


def parse_version(text: str) -> Optional[Tuple[int, int, int]]:
    """Parse ``MAJOR.MINOR.PATCH`` into a sortable tuple, or None if malformed.

    Pre-release/build metadata is tolerated but not ordered — a release tag in
    this repo has never carried any, and inventing an ordering for one would be
    guessing at a convention that does not exist.
    """
    match = _SEMVER_RE.match(text.strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def tag_version(tag: str, agent_id: str) -> Optional[str]:
    """Return the version a release tag encodes, or None if it isn't one.

    Mirrors ``release_agent_<id>.yml``'s own parsing: strip the
    ``agent-pkg-<id>-`` prefix, then an optional leading ``v``. Both
    ``agent-pkg-email-v0.5.0`` and ``agent-pkg-email-0.6.0`` are real releases.
    """
    prefix = f"{_TAG_PREFIX}{agent_id}-"
    if not tag.startswith(prefix):
        return None
    remainder = tag[len(prefix) :]
    if remainder.startswith("v"):
        remainder = remainder[1:]
    return remainder if parse_version(remainder) else None


def latest_release(tags: Sequence[str], agent_id: str) -> Optional[Tuple[str, str]]:
    """Newest ``(version, tag)`` for an agent by semver order, or None.

    Sorting is on the parsed tuple — never the string — so ``0.10.0`` correctly
    outranks ``0.9.0``.
    """
    candidates = []
    for tag in tags:
        version = tag_version(tag, agent_id)
        if version is None:
            continue
        parsed = parse_version(version)
        if parsed is not None:
            candidates.append((parsed, version, tag))
    if not candidates:
        return None
    _, version, tag = max(candidates, key=lambda item: item[0])
    return version, tag


def _git(args: Sequence[str], repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {repo_root}: "
            f"{result.stderr.strip() or 'no stderr'}"
        )
    return result.stdout


def git_tags(repo_root: Path = REPO_ROOT) -> List[str]:
    return [line for line in _git(["tag", "--list"], repo_root).splitlines() if line]


def commits_since(ref: str, path: str, repo_root: Path = REPO_ROOT) -> List[Commit]:
    """Commits touching ``path`` after ``ref``, oldest first."""
    out = _git(
        ["log", "--reverse", "--format=%H%x1f%cI%x1f%s", f"{ref}..HEAD", "--", path],
        repo_root,
    )
    commits = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, when, subject = line.split("\x1f", 2)
        commits.append(
            Commit(sha=sha[:8], when=datetime.fromisoformat(when), subject=subject)
        )
    return commits


def discover_agents(agents_dir: Path = AGENTS_DIR) -> List[str]:
    """Agent ids that ship a manifest, sorted."""
    if not agents_dir.is_dir():
        return []
    return sorted(
        child.name
        for child in agents_dir.iterdir()
        if (child / "python" / "gaia-agent.yaml").is_file()
    )


def manifest_version(agent_id: str, agents_dir: Path = AGENTS_DIR) -> Optional[str]:
    """The ``version:`` line from an agent's manifest.

    Read with a regex rather than a YAML parse so the check keeps working in a
    bare clone with no third-party dependencies installed.
    """
    manifest = agents_dir / agent_id / "python" / "gaia-agent.yaml"
    if not manifest.is_file():
        return None
    for line in manifest.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^version:\s*['\"]?([^'\"\s#]+)", line)
        if match:
            return match.group(1)
    return None


def check_agent(
    agent_id: str,
    tags: Sequence[str],
    max_age_days: int,
    now: datetime,
    repo_root: Path = REPO_ROOT,
    agents_dir: Path = AGENTS_DIR,
) -> Finding:
    release = latest_release(tags, agent_id)
    if release is None:
        # Never released: there is no publish lane for it to be behind. Teaching
        # templates live here too, and flagging them would be noise that trains
        # people to ignore the check.
        return Finding(agent_id, "never-released", "no release tag yet — not gated")

    version, tag = release
    package_path = f"hub/agents/{agent_id}/"
    commits = commits_since(tag, package_path, repo_root)
    if not commits:
        return Finding(agent_id, "ok", f"up to date with {tag}")

    oldest = commits[0]
    age = now - oldest.when
    age_days = age // timedelta(days=1)
    detail = (
        f"{len(commits)} commit(s) since {tag} ({version}); "
        f"oldest {oldest.sha} is {age_days}d old — {oldest.subject}"
    )

    declared = manifest_version(agent_id, agents_dir)
    if declared and declared != version:
        detail += (
            f"\n    manifest says {declared} but the newest tag is {version} — "
            "a version bump merged without a release tag behind it"
        )

    if age > timedelta(days=max_age_days):
        return Finding(
            agent_id,
            "drift",
            f"{detail}\n    SLA is {max_age_days}d. Cut a release: "
            f"tag agent-pkg-{agent_id}-v<version> from main.",
        )
    return Finding(agent_id, "pending", detail)


def run_check(
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: Optional[datetime] = None,
    repo_root: Path = REPO_ROOT,
    agents_dir: Path = AGENTS_DIR,
) -> Tuple[List[Finding], str]:
    now = now or datetime.now(timezone.utc)
    tags = git_tags(repo_root)
    findings = [
        check_agent(agent_id, tags, max_age_days, now, repo_root, agents_dir)
        for agent_id in discover_agents(agents_dir)
    ]
    return findings, format_report(findings)


_LABELS: Dict[str, str] = {
    "drift": "DRIFT",
    "pending": "pending",
    "ok": "ok",
    "never-released": "skipped",
}


def format_report(findings: Sequence[Finding]) -> str:
    lines = []
    for finding in findings:
        lines.append(
            f"  [{_LABELS[finding.status]:>8}] {finding.agent_id}: {finding.message}"
        )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=DEFAULT_MAX_AGE_DAYS,
        help=f"days an unpublished commit may sit before failing (default {DEFAULT_MAX_AGE_DAYS})",
    )
    parser.add_argument(
        "--github-annotations",
        action="store_true",
        help="also emit ::error:: lines for GitHub Actions",
    )
    args = parser.parse_args(argv)

    # Passed explicitly rather than left to the defaults: a default argument
    # binds at import, which would ignore any later override of these.
    findings, report = run_check(
        max_age_days=args.max_age_days,
        repo_root=REPO_ROOT,
        agents_dir=AGENTS_DIR,
    )
    print("Agent release drift check\n")
    print(report or "  (no agent manifests found)")

    errors = [f for f in findings if f.is_error]
    if args.github_annotations:
        for finding in errors:
            flat = finding.message.replace("\n", " ").strip()
            print(f"::error::{finding.agent_id} release drift — {flat}")

    print()
    print(f"{len(errors)} agent(s) past the {args.max_age_days}d publish SLA.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
