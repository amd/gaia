# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Top-level harness glue for running GAIA-Internal-20 via the CLI runner.

Pulls the three pieces (runner + suite loader + scorer) together into
one ``run_suite`` entry point that:

1. Loads task files (all, or one if ``task_id`` given).
2. For each task, prepares a sandbox (git worktree of ``coder``),
   applies the fixture, spawns the daemon, sends the task, waits,
   collects artifacts, scores, tears down.
3. Returns a JSON-serialisable report dict.

The bulk subprocess logic is deliberately **not** on a class — a flat
function is easier for ``gaia.cli`` to import without circular deps
and easier for ``tests/eval/`` to exercise end-to-end.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from gaia.eval.runners.coder_cli import CoderCLIRunner, in_tree_runner
from gaia.eval.suites.gaia_internal_20 import (
    SUITE_ID,
    TaskMeta,
    load_all_tasks,
    load_task,
)
from gaia.eval.suites.gaia_internal_20.scorer import (
    ScorecardRow,
    apply_fixture,
    score_task,
)
from gaia.logger import get_logger

log = get_logger(__name__)

# Default branch / ref that the sandbox worktree checks out. Matches
# §10.2 ("``git worktree add $SANDBOX coder`` on the bound repo").
_DEFAULT_SANDBOX_REF = "coder"


@dataclass
class SuiteReport:
    """Full report for one suite run — the output of ``run_suite``."""

    suite_id: str
    run_started_at: str
    run_finished_at: str
    target: str
    rows: list[dict] = field(default_factory=list)
    aggregate: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "suite_id": self.suite_id,
            "run_started_at": self.run_started_at,
            "run_finished_at": self.run_finished_at,
            "target": self.target,
            "rows": self.rows,
            "aggregate": self.aggregate,
        }


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def run_suite(
    *,
    target: str = "gaia-coder",
    task_id: Optional[str] = None,
    repo_root: Path,
    sandbox_ref: str = _DEFAULT_SANDBOX_REF,
    tier: int = 0,
    timeout_min: float = 20.0,
    output: Optional[Path] = None,
    runner: Optional[CoderCLIRunner] = None,
    skip_worktree: bool = False,
    sandbox_override: Optional[Path] = None,
) -> SuiteReport:
    """Run the full suite (or a single task) and return a report.

    Parameters
    ----------
    target:
        Eval target. For v1 only ``"gaia-coder"`` is supported.
    task_id:
        Run only this task (e.g. ``"T18"``). ``None`` runs the full suite.
    repo_root:
        Path to a clone of the bound repo. Used as the source for
        ``git worktree add`` and the ``pr_mergeable`` check.
    sandbox_ref:
        Ref to check out into the sandbox. Defaults to ``coder``.
    tier:
        Capability tier passed to ``gaia-coder daemon``.
    timeout_min:
        Per-task wall-clock ceiling.
    output:
        If set, write the JSON report here.
    runner:
        Optional preconfigured :class:`CoderCLIRunner`. Defaults to the
        in-tree runner (``python -m gaia.coder.cli``).
    skip_worktree:
        If True, use ``sandbox_override`` as the sandbox and do not
        create/remove a worktree. Used by tests that drive a single
        task against a pre-existing sandbox.
    sandbox_override:
        Used when ``skip_worktree`` is True.
    """
    if target != "gaia-coder":
        raise ValueError(
            f"run_suite: only target='gaia-coder' is supported in v1 "
            f"(got {target!r})"
        )

    if runner is None:
        runner = in_tree_runner()

    tasks: list[TaskMeta] = [load_task(task_id)] if task_id else load_all_tasks()
    if not tasks:
        raise RuntimeError(f"{SUITE_ID}: no tasks found (task_id={task_id!r})")

    started = datetime.now(timezone.utc)
    rows: list[ScorecardRow] = []
    for task in tasks:
        log.info("Running %s: %s", task.id, task.title)
        row = _run_one_task(
            task=task,
            runner=runner,
            repo_root=repo_root,
            sandbox_ref=sandbox_ref,
            tier=tier,
            timeout_min=timeout_min,
            skip_worktree=skip_worktree,
            sandbox_override=sandbox_override,
        )
        rows.append(row)

    finished = datetime.now(timezone.utc)
    report = SuiteReport(
        suite_id=SUITE_ID,
        run_started_at=started.isoformat(),
        run_finished_at=finished.isoformat(),
        target=target,
        rows=[row.to_dict() for row in rows],
        aggregate=_aggregate(rows),
    )

    if output is not None:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info("Wrote report → %s", output)

    return report


# ---------------------------------------------------------------------------
# Internals.
# ---------------------------------------------------------------------------


def _run_one_task(
    *,
    task: TaskMeta,
    runner: CoderCLIRunner,
    repo_root: Path,
    sandbox_ref: str,
    tier: int,
    timeout_min: float,
    skip_worktree: bool,
    sandbox_override: Optional[Path],
) -> ScorecardRow:
    """Run one task end-to-end and return its scorecard row."""
    # ---- 1. Prepare sandbox ----
    if skip_worktree:
        if sandbox_override is None:
            raise ValueError("skip_worktree=True requires sandbox_override to be set")
        sandbox = Path(sandbox_override).resolve()
        created_worktree: Optional[Path] = None
    else:
        created_worktree = _create_worktree(repo_root, sandbox_ref)
        sandbox = created_worktree

    try:
        # ---- 2. Apply fixture if any ----
        fixture_err = apply_fixture(task, sandbox)
        if fixture_err:
            log.warning("%s: fixture apply reported: %s", task.id, fixture_err)

        # ---- 3. Spawn → ask → wait → collect → stop ----
        result, artifacts = runner.run_one(
            sandbox=sandbox,
            task_md_path=task.path,
            tier=tier,
            no_network_writes=True,
            timeout_min=timeout_min,
        )

        # ---- 4. Score ----
        row = score_task(
            task_meta=task,
            artifacts=artifacts,
            sandbox=sandbox,
            coder_repo=repo_root,
            coder_ref=sandbox_ref,
        )
        row.notes.append(f"wait_returncode={result.wait_returncode}")
        if result.timed_out:
            row.notes.append("daemon wait TIMED OUT — task did not complete")
        return row
    finally:
        if created_worktree is not None:
            _cleanup_worktree(repo_root, created_worktree)


def _create_worktree(repo_root: Path, ref: str) -> Path:
    """Create a fresh git worktree checked out at ``ref``.

    Uses ``tempfile.mkdtemp`` so concurrent suite runs don't collide
    on path. The caller is responsible for cleanup via
    :func:`_cleanup_worktree`.
    """
    sandbox = Path(tempfile.mkdtemp(prefix=f"gaia-eval-{SUITE_ID}-"))
    # mkdtemp creates the dir; git worktree add wants to CREATE the
    # target, so remove it first. Race-free because the dir is still
    # uniquely ours.
    sandbox.rmdir()
    subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "add", str(sandbox), ref],
        check=True,
        capture_output=True,
        text=True,
        timeout=60.0,
    )
    return sandbox


def _cleanup_worktree(repo_root: Path, sandbox: Path) -> None:
    """Best-effort worktree + directory cleanup. Never raises."""
    try:
        subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "worktree",
                "remove",
                "--force",
                str(sandbox),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30.0,
        )
    except Exception as e:  # noqa: BLE001 — cleanup path, log-and-move-on
        log.warning("worktree remove failed for %s: %s", sandbox, e)


def _aggregate(rows: list[ScorecardRow]) -> dict:
    """Return suite-level aggregate stats."""
    if not rows:
        return {"total": 0, "passed": 0, "avg_score": 0.0, "pass_rate": 0.0}
    passed = sum(1 for r in rows if r.passed)
    total = len(rows)
    avg = round(sum(r.score for r in rows) / total, 4)
    return {
        "total": total,
        "passed": passed,
        "avg_score": avg,
        "pass_rate": round(passed / total, 4),
    }
