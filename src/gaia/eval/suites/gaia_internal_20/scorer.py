# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Deterministic scorer for GAIA-Internal-20 tasks (§10.2).

Per the spec rubric::

    score = (compiles * 0.2) + (tests_pass * 0.4)
          + (no_lint_regression * 0.2) + (pr_mergeable * 0.2)

Each check is 0 or 1. Task passes if ``score >= 0.8``. Suite score is
the average of task scores.

The scorer runs each check as a subprocess (except ``pr_mergeable``,
which is resolved against the ``coder`` branch of the bound repo via
``git apply --check``). Checks are deliberately shell commands so
tasks can declare arbitrary verification (e.g. ``pytest
tests/test_webui.py -x``) without a Python API.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from gaia.eval.suites.gaia_internal_20 import (
    DEFAULT_CHECK_WEIGHTS,
    PASS_THRESHOLD,
    CheckSpec,
    TaskMeta,
)
from gaia.logger import get_logger

log = get_logger(__name__)


# Marker string used by tasks to request the special "does this diff
# merge cleanly against coder HEAD?" check. Kept short and memorable.
PR_MERGEABLE_MARKER = "diff_applies_to_coder_cleanly"

# Default timeout for any single check subprocess. Individual tasks
# can have long-running test suites but not THIS long — a check that
# doesn't finish in 10 min is almost certainly hung.
_CHECK_TIMEOUT_S = 600.0


# ---------------------------------------------------------------------------
# Result dataclasses.
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """Outcome of one check ran by the scorer."""

    name: str
    passed: bool  # 0 or 1 in the rubric — bool in Python
    weight: float
    returncode: Optional[int] = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    skipped: bool = False
    skipped_reason: Optional[str] = None

    @property
    def score(self) -> float:
        """Weighted contribution to the task's total score."""
        if self.skipped:
            return 0.0
        return self.weight if self.passed else 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "weight": self.weight,
            "returncode": self.returncode,
            "skipped": self.skipped,
            "skipped_reason": self.skipped_reason,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "score": round(self.score, 4),
        }


@dataclass
class ScorecardRow:
    """Per-task row of the suite scorecard."""

    task_id: str
    title: str
    score: float
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "score": round(self.score, 4),
            "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
            "artifacts": self.artifacts,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


def score_task(
    task_meta: TaskMeta,
    artifacts: dict[str, Path],
    sandbox: Path,
    *,
    coder_repo: Optional[Path] = None,
    coder_ref: str = "coder",
    check_timeout_s: float = _CHECK_TIMEOUT_S,
) -> ScorecardRow:
    """Score one task's artifacts per the §10.2 rubric.

    Parameters
    ----------
    task_meta:
        Parsed task front-matter + body.
    artifacts:
        ``{filename: Path}`` map returned by
        :meth:`CoderCLIRunner.collect_artifacts`.
    sandbox:
        The sandbox directory in which the agent ran. Checks that
        reference repo paths (``pytest tests/...``) execute with
        ``cwd=sandbox``.
    coder_repo:
        Path to a clone of the bound repo for the ``pr_mergeable``
        check (``git apply --check`` uses this as its working tree).
        Defaults to ``sandbox`` — they are the same thing in a real
        eval flow.
    coder_ref:
        Git ref to validate ``diff.patch`` against. Defaults to
        ``coder`` per §10.2 ("diff applies to coder cleanly").
    """
    coder_repo = coder_repo or sandbox
    checks: list[CheckResult] = []

    # If the task declared no scoring block, fall back to the four
    # default checks with a sensible no-op placeholder. This makes the
    # scorer robust against placeholder task files (TODO: expand
    # description) without silently returning 0.
    specs = task_meta.checks or _default_check_specs()

    for spec in specs:
        result = _run_check(
            spec, task_meta, artifacts, sandbox, coder_repo, coder_ref, check_timeout_s
        )
        checks.append(result)

    total_score = sum(c.score for c in checks)
    # Cap at 1.0 to avoid float-sum artefacts.
    total_score = round(min(total_score, 1.0), 4)
    passed = total_score >= PASS_THRESHOLD

    return ScorecardRow(
        task_id=task_meta.id,
        title=task_meta.title,
        score=total_score,
        passed=passed,
        checks=checks,
        artifacts={
            name: str(path) for name, path in artifacts.items() if path.exists()
        },
    )


def weighted_sum(checks: list[CheckResult]) -> float:
    """Sum each check's ``score`` property (test helper + public API)."""
    return round(min(sum(c.score for c in checks), 1.0), 4)


def pr_mergeable(
    diff_patch: Path,
    repo: Path,
    ref: str = "coder",
) -> tuple[bool, str, str]:
    """Return ``(mergeable, stdout, stderr)`` for ``git apply --check``.

    An empty (zero-byte) diff is treated as mergeable (no changes) —
    matches ``git apply --check`` behaviour and means the stub daemon's
    empty ``diff.patch`` scores 1 for this check.
    """
    diff_patch = Path(diff_patch)
    if not diff_patch.exists():
        return False, "", f"diff file not found: {diff_patch}"
    if diff_patch.stat().st_size == 0:
        return True, "", "empty diff — trivially mergeable"

    # Resolve the ref's tree first so the check runs against the
    # declared ``coder`` ref, not whatever happens to be checked out.
    rev_parse = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", ref],
        capture_output=True,
        text=True,
        check=False,
        timeout=30.0,
    )
    if rev_parse.returncode != 0:
        return (
            False,
            rev_parse.stdout,
            f"ref {ref!r} not found in repo {repo}: {rev_parse.stderr.strip()}",
        )

    apply_check = subprocess.run(
        ["git", "-C", str(repo), "apply", "--check", str(diff_patch)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60.0,
    )
    return (
        apply_check.returncode == 0,
        apply_check.stdout,
        apply_check.stderr,
    )


# ---------------------------------------------------------------------------
# Internals.
# ---------------------------------------------------------------------------


def _default_check_specs() -> list[CheckSpec]:
    """Return the four default check specs (used for placeholder tasks)."""
    return [
        CheckSpec(name="compiles", check=":", weight=DEFAULT_CHECK_WEIGHTS["compiles"]),
        CheckSpec(
            name="tests_pass", check=":", weight=DEFAULT_CHECK_WEIGHTS["tests_pass"]
        ),
        CheckSpec(
            name="no_lint_regression",
            check=":",
            weight=DEFAULT_CHECK_WEIGHTS["no_lint_regression"],
        ),
        CheckSpec(
            name="pr_mergeable",
            check=PR_MERGEABLE_MARKER,
            weight=DEFAULT_CHECK_WEIGHTS["pr_mergeable"],
        ),
    ]


def _run_check(
    spec: CheckSpec,
    task_meta: TaskMeta,
    artifacts: dict[str, Path],
    sandbox: Path,
    coder_repo: Path,
    coder_ref: str,
    timeout_s: float,
) -> CheckResult:
    """Execute a single check and return its :class:`CheckResult`."""
    if spec.check == PR_MERGEABLE_MARKER or spec.name == "pr_mergeable":
        diff_patch = artifacts.get("diff.patch")
        if diff_patch is None:
            return CheckResult(
                name=spec.name,
                passed=False,
                weight=spec.weight,
                skipped=True,
                skipped_reason="diff.patch artifact missing",
            )
        mergeable, stdout, stderr = pr_mergeable(diff_patch, coder_repo, coder_ref)
        return CheckResult(
            name=spec.name,
            passed=mergeable,
            weight=spec.weight,
            returncode=0 if mergeable else 1,
            stdout_tail=_tail(stdout),
            stderr_tail=_tail(stderr),
        )

    # Generic subprocess check — shell out to the declared command.
    # Use ``shell=True`` because tasks declare pipelines ("pytest ...
    # | grep ..."); ``shlex.split`` would fail on those. The scorer
    # only ever runs commands from checked-in task files — this is
    # developer-trusted input, not remote data.
    try:
        completed = subprocess.run(
            spec.check,
            cwd=sandbox,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as e:
        return CheckResult(
            name=spec.name,
            passed=False,
            weight=spec.weight,
            returncode=None,
            stdout_tail=_tail(e.stdout or ""),
            stderr_tail=_tail(e.stderr or f"check timed out after {timeout_s}s"),
        )
    return CheckResult(
        name=spec.name,
        passed=completed.returncode == 0,
        weight=spec.weight,
        returncode=completed.returncode,
        stdout_tail=_tail(completed.stdout),
        stderr_tail=_tail(completed.stderr),
    )


def _tail(text: str, max_chars: int = 800) -> str:
    """Return the last ``max_chars`` of ``text`` — scorecard uses tails."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return "…" + text[-max_chars:]


# --- fixture application ---------------------------------------------------


def apply_fixture(task_meta: TaskMeta, sandbox: Path) -> Optional[str]:
    """Apply the task's seeded-bug fixture, if any.

    Returns ``None`` on success (or when there is no fixture to apply),
    a human-readable reason string on failure (the caller decides
    whether that's fatal). Using ``git apply`` means the fixture can
    be reviewed as a normal diff.

    Phase 2 stub fixtures are zero-byte files (see
    ``fixtures/README.md``) — we treat those as no-ops so the harness
    plumbing is exercised even before real seeded bugs are written.
    """
    if not task_meta.has_fixture:
        return None
    fp = task_meta.fixture_path()
    if fp is None or not fp.exists():
        return f"declared fixture not found on disk: {fp}"
    if fp.stat().st_size == 0:
        # Phase 2 stub fixture — nothing to apply. Not an error.
        return None
    completed = subprocess.run(
        ["git", "-C", str(sandbox), "apply", str(fp)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30.0,
    )
    if completed.returncode != 0:
        return (
            f"git apply {fp.name} failed "
            f"(rc={completed.returncode}): {completed.stderr.strip()}"
        )
    return None


# Public surface — kept explicit for consumers.
__all__ = [
    "PR_MERGEABLE_MARKER",
    "CheckResult",
    "ScorecardRow",
    "score_task",
    "weighted_sum",
    "pr_mergeable",
    "apply_fixture",
]
