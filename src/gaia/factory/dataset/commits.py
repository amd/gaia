# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Measure whether a session's commit can be recovered — and proven right.

A record references files at a point in history, so the obvious move is to read
them back with ``git show <commit>:<path>``.  Transcripts record ``gitBranch``
and a timestamp but never a commit, so the commit has to be inferred with
``git rev-list -n1 --before=<timestamp> <branch>``.

**That inference produces confident wrong answers.**  On this corpus it returned
the *same* commit for four different session branches, because those branches
were merged and the walk lands on a shared ancestor.  So it is never trusted on
its own: a recovered commit counts only when content read back from it
byte-matches content the transcript independently holds for the same file.

This module measures that verification rate.  It writes nothing into the dataset
— the decision it informs is whether ``repo.commit`` is a usable field at all.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from gaia.factory.dataset.extract import iter_transcripts, scan_transcript

#: A git call that has not answered in this long is not going to.
_TIMEOUT = 20


def _git(repo: Path, *args: str) -> Optional[str]:
    """Run git, returning stdout or ``None``. Never raises on a git-level failure.

    A missing branch, a pruned worktree and a deleted repository are all expected
    here — this module's whole job is to find out how often that happens — so
    they are counted, not raised.  A missing ``git`` binary is a different thing
    and is allowed to surface.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            # Repository content is UTF-8; without this, Python decodes with the
            # locale codec (cp1252 on Windows) and one undecodable byte kills the
            # whole measurement rather than one file.
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def find_repo_root(cwd: str) -> Optional[Path]:
    """Walk up from a recorded working directory to a git root that still exists."""
    if not cwd:
        return None
    path = Path(cwd)
    for candidate in [path, *path.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def infer_commit(repo: Path, branch: str, timestamp: str) -> Optional[str]:
    """Best guess at the commit live at ``timestamp`` on ``branch``. Unverified."""
    if not branch or branch in ("HEAD", ""):
        return None
    out = _git(repo, "rev-list", "-n", "1", f"--before={timestamp}", branch)
    if not out:
        return None
    commit = out.strip().splitlines()
    return commit[0] if commit else None


def verify_commit(
    repo: Path, commit: str, file_path: str, expected: str
) -> Optional[bool]:
    """Does ``file_path`` at ``commit`` match what the transcript says was read?

    Returns ``None`` when the file is not in that commit at all — absence is not
    disagreement, and counting it as a mismatch would understate the rate.
    """
    try:
        relative = Path(file_path).resolve().relative_to(repo.resolve())
    except (ValueError, OSError):
        return None
    out = _git(repo, "show", f"{commit}:{relative.as_posix()}")
    if out is None:
        return None
    return out.replace("\r\n", "\n").strip() == expected.replace("\r\n", "\n").strip()


@dataclass
class RecoveryStats:
    """How far commit recovery got, stage by stage."""

    sessions: int = 0
    repo_root_found: int = 0
    repo_root_missing: int = 0
    branch_unusable: int = 0
    commit_inferred: int = 0
    verification_attempted: int = 0
    verified_match: int = 0
    verified_mismatch: int = 0
    file_absent_at_commit: int = 0
    no_transcript_content_to_check: int = 0
    distinct_commits: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        attempted = max(self.verification_attempted, 1)
        collisions = sum(1 for n in self.distinct_commits.values() if n > 1)
        return {
            "sessions_sampled": self.sessions,
            "repo_root_found": self.repo_root_found,
            "repo_root_missing": self.repo_root_missing,
            "branch_unusable": self.branch_unusable,
            "commit_inferred": self.commit_inferred,
            "verification_attempted": self.verification_attempted,
            "verified_match": self.verified_match,
            "verified_mismatch": self.verified_mismatch,
            "file_absent_at_commit": self.file_absent_at_commit,
            "no_transcript_content_to_check": self.no_transcript_content_to_check,
            "pct_verified_of_attempted": round(
                100.0 * self.verified_match / attempted, 1
            ),
            "pct_verified_of_sessions": round(
                100.0 * self.verified_match / max(self.sessions, 1), 1
            ),
            "commits_claimed_by_more_than_one_session": collisions,
            "note": (
                "A commit counts as recovered only when content read back from it "
                "byte-matches content the transcript independently holds. An "
                "inferred-but-unverified commit is a confident guess: rev-list "
                "--before returns a shared ancestor for merged branches, so "
                "several sessions resolve to one commit."
            ),
        }


def measure_recovery(
    session_ids: List[str], projects_root: Path, limit: int = 60
) -> Tuple[RecoveryStats, List[Dict[str, object]]]:
    """Attempt commit recovery for up to ``limit`` sessions and report the rate."""
    stats = RecoveryStats()
    detail: List[Dict[str, object]] = []
    seen: set = set()

    for session_id, path, scope in iter_transcripts(session_ids, projects_root):
        if scope != "main" or session_id in seen or len(seen) >= limit:
            continue
        seen.add(session_id)
        scan = scan_transcript(path)
        if scan is None:
            continue
        stats.sessions += 1

        repo = find_repo_root(scan.cwd)
        if repo is None:
            stats.repo_root_missing += 1
            detail.append({"session": session_id[:8], "outcome": "repo_root_missing"})
            continue
        stats.repo_root_found += 1

        commit = infer_commit(repo, scan.git_branch, scan.started_at)
        if not commit:
            stats.branch_unusable += 1
            detail.append({"session": session_id[:8], "outcome": "branch_unusable"})
            continue
        stats.commit_inferred += 1
        stats.distinct_commits[commit] = stats.distinct_commits.get(commit, 0) + 1

        probe = None
        for point in scan.decisions:
            for obs in point.observations:
                if obs.file_path and obs.file_content and obs.ok:
                    probe = (obs.file_path, obs.file_content)
                    break
            if probe:
                break
        if probe is None:
            stats.no_transcript_content_to_check += 1
            detail.append(
                {"session": session_id[:8], "outcome": "nothing_to_verify_against"}
            )
            continue

        stats.verification_attempted += 1
        result = verify_commit(repo, commit, probe[0], probe[1])
        if result is None:
            stats.file_absent_at_commit += 1
            outcome = "file_absent_at_commit"
        elif result:
            stats.verified_match += 1
            outcome = "verified"
        else:
            stats.verified_mismatch += 1
            outcome = "mismatch"
        detail.append({"session": session_id[:8], "outcome": outcome})

    return stats, detail
