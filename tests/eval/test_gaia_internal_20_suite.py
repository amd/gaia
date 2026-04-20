# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for the :mod:`gaia.eval.suites.gaia_internal_20` harness."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gaia.coder.cli import ARTIFACT_FILENAMES
from gaia.eval.runners.coder_cli import in_tree_runner
from gaia.eval.suites.gaia_internal_20 import (
    DEFAULT_CHECK_WEIGHTS,
    PASS_THRESHOLD,
    CheckSpec,
    TaskMeta,
    load_all_tasks,
    load_task,
    parse_task_file,
)
from gaia.eval.suites.gaia_internal_20.harness import run_suite
from gaia.eval.suites.gaia_internal_20.scorer import (
    CheckResult,
    pr_mergeable,
    score_task,
    weighted_sum,
)

# ---------------------------------------------------------------------------
# Suite-loader tests.
# ---------------------------------------------------------------------------


def test_suite_loads_20_tasks() -> None:
    """The suite directory has exactly 20 T##.md files and they all parse."""
    tasks = load_all_tasks()
    assert len(tasks) == 20, f"expected 20 tasks, got {len(tasks)}"
    ids = [t.id for t in tasks]
    assert ids == [
        f"T{i:02d}" for i in range(1, 21)
    ], f"tasks not in T01..T20 order: {ids}"


def test_task_front_matter_parses() -> None:
    """Every required field is present and correctly typed."""
    for task in load_all_tasks():
        assert isinstance(task, TaskMeta)
        assert task.id.startswith("T")
        assert task.title, f"{task.id}: title is empty"
        assert task.expected_fix_class, f"{task.id}: missing expected_fix_class"
        assert task.max_diff_loc > 0, f"{task.id}: max_diff_loc must be positive"
        assert (
            task.max_wall_clock_min > 0
        ), f"{task.id}: max_wall_clock_min must be positive"
        assert task.checks, f"{task.id}: no scoring block"
        # Every check has a non-negative weight.
        for c in task.checks:
            assert isinstance(c, CheckSpec)
            assert c.weight >= 0.0, f"{task.id}: check {c.name} has negative weight"


def test_load_task_single() -> None:
    """``load_task('T18')`` round-trips through parse_task_file."""
    t18 = load_task("T18")
    assert t18.id == "T18"
    assert "except Exception" in t18.title
    # T18 seeds a fixture.
    assert t18.has_fixture
    fp = t18.fixture_path()
    assert fp is not None and fp.exists()


def test_parse_task_file_rejects_missing_front_matter(tmp_path: Path) -> None:
    bad = tmp_path / "Tbad.md"
    bad.write_text("# No front matter\n", encoding="utf-8")
    with pytest.raises(ValueError, match="front-matter"):
        parse_task_file(bad)


def test_parse_task_file_rejects_unterminated_front_matter(tmp_path: Path) -> None:
    bad = tmp_path / "Tbad.md"
    bad.write_text("---\nid: Tbad\n# no closing delimiter\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unterminated"):
        parse_task_file(bad)


# ---------------------------------------------------------------------------
# Scorer tests.
# ---------------------------------------------------------------------------


def test_default_check_weights_sum_to_one() -> None:
    """Sanity check on the §10.2 rubric — weights sum to 1.0."""
    total = sum(DEFAULT_CHECK_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9, f"weights sum to {total} (want 1.0)"


def test_scorer_weighted_sum() -> None:
    """Given mock check results, computes the expected weighted sum."""
    checks = [
        CheckResult(name="compiles", passed=True, weight=0.2),
        CheckResult(name="tests_pass", passed=True, weight=0.4),
        CheckResult(name="no_lint_regression", passed=False, weight=0.2),
        CheckResult(name="pr_mergeable", passed=True, weight=0.2),
    ]
    # Expected: 0.2 + 0.4 + 0 + 0.2 = 0.8 → exactly at threshold.
    assert weighted_sum(checks) == pytest.approx(0.8)
    # All-pass = 1.0.
    for c in checks:
        c.passed = True
    assert weighted_sum(checks) == pytest.approx(1.0)
    # All-fail = 0.0.
    for c in checks:
        c.passed = False
    assert weighted_sum(checks) == pytest.approx(0.0)
    # Skipped checks contribute 0 regardless of passed flag.
    checks[0].skipped = True
    checks[0].passed = True
    assert weighted_sum(checks) == pytest.approx(0.0)


def test_pass_threshold_matches_spec() -> None:
    assert PASS_THRESHOLD == 0.8


def test_pr_mergeable_empty_diff_is_trivially_mergeable(tmp_path: Path) -> None:
    """Empty diff file → treated as mergeable (matches git apply behaviour)."""
    repo = _init_git_repo(tmp_path / "repo")
    empty_diff = tmp_path / "empty.patch"
    empty_diff.write_text("", encoding="utf-8")
    mergeable, _stdout, stderr = pr_mergeable(empty_diff, repo, ref="HEAD")
    assert (
        mergeable is True
    ), f"empty diff should be trivially mergeable (stderr={stderr!r})"


def test_pr_mergeable_rejects_invalid_patch(tmp_path: Path) -> None:
    """A patch that references missing files fails `git apply --check`."""
    repo = _init_git_repo(tmp_path / "repo")
    bogus = tmp_path / "bogus.patch"
    # Patch claims to modify a file that doesn't exist in the repo.
    bogus.write_text(
        "diff --git a/does-not-exist.txt b/does-not-exist.txt\n"
        "--- a/does-not-exist.txt\n"
        "+++ b/does-not-exist.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n",
        encoding="utf-8",
    )
    mergeable, _stdout, stderr = pr_mergeable(bogus, repo, ref="HEAD")
    assert (
        mergeable is False
    ), f"bogus patch should fail git apply --check (stderr={stderr!r})"


def test_pr_mergeable_missing_file_returns_false(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path / "repo")
    mergeable, _stdout, stderr = pr_mergeable(tmp_path / "nope.patch", repo, ref="HEAD")
    assert mergeable is False
    assert "not found" in stderr


# ---------------------------------------------------------------------------
# Check-runner test (score_task end-to-end against synthetic artifacts).
# ---------------------------------------------------------------------------


def test_score_task_with_synthetic_artifacts(tmp_path: Path) -> None:
    """Synthetic artifacts + a trivial task produce the expected score."""
    repo = _init_git_repo(tmp_path / "repo")
    # Write a task spec that uses ``true`` / ``false`` for checks so we
    # can deterministically verify the weighted sum.
    task_path = tmp_path / "Tsynth.md"
    task_path.write_text(
        "---\n"
        "id: Tsynth\n"
        "title: Synthetic task\n"
        "expected_fix_class: tool\n"
        "max_diff_loc: 10\n"
        "max_wall_clock_min: 1\n"
        "scoring:\n"
        "  - name: compiles\n"
        "    check: 'true'\n"
        "    weight: 0.2\n"
        "  - name: tests_pass\n"
        "    check: 'true'\n"
        "    weight: 0.4\n"
        "  - name: no_lint_regression\n"
        "    check: 'false'\n"
        "    weight: 0.2\n"
        "  - name: pr_mergeable\n"
        "    check: 'diff_applies_to_coder_cleanly'\n"
        "    weight: 0.2\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    task = parse_task_file(task_path)

    # Stub artifacts directory — empty diff.patch so pr_mergeable passes.
    artifact_dir = repo / ".eval-artifacts" / "Tsynth"
    artifact_dir.mkdir(parents=True)
    for name in ARTIFACT_FILENAMES:
        (artifact_dir / name).write_text("", encoding="utf-8")
    artifacts = {name: artifact_dir / name for name in ARTIFACT_FILENAMES}

    row = score_task(
        task_meta=task,
        artifacts=artifacts,
        sandbox=repo,
        coder_repo=repo,
        coder_ref="HEAD",
    )
    # 0.2 (compiles) + 0.4 (tests_pass) + 0 (lint) + 0.2 (pr) = 0.8 → passed
    assert row.score == pytest.approx(0.8)
    assert row.passed is True
    # Names preserved in order.
    assert [c.name for c in row.checks] == [
        "compiles",
        "tests_pass",
        "no_lint_regression",
        "pr_mergeable",
    ]


# ---------------------------------------------------------------------------
# PR-mergeable explicit check (required test).
# ---------------------------------------------------------------------------


def test_pr_mergeable_check(tmp_path: Path) -> None:
    """`git apply --check` against HEAD — success AND failure cases."""
    repo = _init_git_repo(tmp_path / "repo")

    # Success: patch adds a new file.
    new_file_patch = tmp_path / "add.patch"
    new_file_patch.write_text(
        "diff --git a/new.txt b/new.txt\n"
        "new file mode 100644\n"
        "index 0000000..9daeafb\n"
        "--- /dev/null\n"
        "+++ b/new.txt\n"
        "@@ -0,0 +1 @@\n"
        "+test\n",
        encoding="utf-8",
    )
    ok, _stdout, stderr = pr_mergeable(new_file_patch, repo, ref="HEAD")
    assert ok is True, f"new-file patch should apply cleanly (stderr={stderr!r})"

    # Failure: patch collides with existing file.
    (repo / "conflict.txt").write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "conflict.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "conflict file"],
        check=True,
        capture_output=True,
    )
    bad_patch = tmp_path / "bad.patch"
    bad_patch.write_text(
        "diff --git a/conflict.txt b/conflict.txt\n"
        "--- a/conflict.txt\n"
        "+++ b/conflict.txt\n"
        "@@ -1 +1 @@\n"
        "-wrong-starting-content\n"
        "+new\n",
        encoding="utf-8",
    )
    bad_ok, _stdout2, _stderr2 = pr_mergeable(bad_patch, repo, ref="HEAD")
    assert (
        bad_ok is False
    ), "patch with wrong context line should fail git apply --check"


# ---------------------------------------------------------------------------
# End-to-end smoke: T18 through the full harness against the stub daemon.
# ---------------------------------------------------------------------------


def test_suite_end_to_end_smoke(tmp_path: Path) -> None:
    """Run T18 through ``run_suite`` against the stub daemon.

    T18 is the simplest task (diff ≤ 15 LoC, ``except Exception: pass``).
    We use ``skip_worktree=True`` + a pre-initialised sandbox so the
    test does not depend on a ``coder`` branch being reachable in CI.
    """
    sandbox = _init_git_repo(tmp_path / "sandbox")
    runner = in_tree_runner()
    report = run_suite(
        target="gaia-coder",
        task_id="T18",
        repo_root=sandbox,
        sandbox_ref="HEAD",
        tier=0,
        timeout_min=1,
        runner=runner,
        skip_worktree=True,
        sandbox_override=sandbox,
    )
    assert report.suite_id == "gaia-internal-20"
    assert len(report.rows) == 1
    row = report.rows[0]
    assert row["task_id"] == "T18"
    # Score is deterministic given the stub daemon's empty artifacts:
    # compiles/tests_pass/no_lint_regression depend on shell commands
    # that may or may not exist in the test environment (we don't
    # pin that), but pr_mergeable MUST pass (empty diff trivially
    # applies).
    pr_check = next(c for c in row["checks"] if c["name"] == "pr_mergeable")
    assert pr_check["passed"] is True, f"pr_mergeable should pass: {pr_check}"
    # Report has the right aggregate shape.
    assert "total" in report.aggregate
    assert "avg_score" in report.aggregate
    assert report.aggregate["total"] == 1


def test_run_suite_rejects_unknown_target(tmp_path: Path) -> None:
    sandbox = _init_git_repo(tmp_path / "sandbox")
    with pytest.raises(ValueError, match="gaia-coder"):
        run_suite(
            target="not-a-real-target",
            task_id="T18",
            repo_root=sandbox,
            runner=in_tree_runner(),
            skip_worktree=True,
            sandbox_override=sandbox,
        )


def test_run_suite_writes_output_json(tmp_path: Path) -> None:
    sandbox = _init_git_repo(tmp_path / "sandbox")
    output = tmp_path / "report.json"
    run_suite(
        target="gaia-coder",
        task_id="T18",
        repo_root=sandbox,
        sandbox_ref="HEAD",
        timeout_min=1,
        runner=in_tree_runner(),
        skip_worktree=True,
        sandbox_override=sandbox,
        output=output,
    )
    assert output.exists()
    parsed = json.loads(output.read_text(encoding="utf-8"))
    assert parsed["suite_id"] == "gaia-internal-20"
    assert parsed["target"] == "gaia-coder"
    assert len(parsed["rows"]) == 1


# ---------------------------------------------------------------------------
# Shared helpers.
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> Path:
    """Initialise an empty git repo at ``path`` with one commit.

    We need a real commit so ``git apply --check`` has a HEAD to check
    against. Keep it tiny — the commit only has a README.
    """
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(path)],
        check=True,
        capture_output=True,
    )
    # Configure a committer identity so ``git commit`` works in CI
    # environments that don't have one globally.
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test Harness"],
        check=True,
    )
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "seed"],
        check=True,
        capture_output=True,
    )
    # The harness defaults to sandbox_ref='coder'; tests use 'HEAD'
    # (and we also create a 'coder' branch at HEAD as a courtesy so
    # tests that accidentally rely on the default still work).
    subprocess.run(
        ["git", "-C", str(path), "branch", "coder"],
        check=True,
        capture_output=True,
    )
    return path
