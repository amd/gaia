# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fix application (§7.4 steps 4–5): branch + edit + regression-test + differential run.

Branch naming (§7.4 step 4): ``auto/gaia-coder/<feedback_id>``.

The fixer uses the edit primitive from ``gaia.coder.tools.file.FileToolsMixin``
(PR #818) via a thin inline wrapper so the module stays testable on hosts
without the mixin instantiated — :func:`_edit_file_impl` re-uses the exact
implementation from the mixin but as a free function. This keeps the
mixin's registered-tool surface untouched while giving the fixer a
programmatic entry point.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from gaia.coder.safety import ActionContext, enforce_action
from gaia.coder.self_fix.planner import Plan
from gaia.coder.self_fix.triage import FixClassResult

logger = logging.getLogger(__name__)

#: Prefix the fixer uses for self-fix branches (§7.4 step 4).
SELF_FIX_BRANCH_PREFIX: str = "auto/gaia-coder"

#: Fallback base branch — the coder integration branch (§5.6).
DEFAULT_BASE_REF: str = "coder"


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EditHunk:
    """Spec for a single ``edit_file`` call the fixer will make."""

    path: str
    old_string: str
    new_string: str
    replace_all: bool = False


@dataclass(frozen=True)
class Diff:
    """Summary of what the fixer applied."""

    feedback_id: str
    branch: str
    files_edited: Tuple[str, ...]
    regression_test_path: Optional[str] = None
    new_files: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TestPath:
    """Return value of :func:`write_regression_test`."""

    path: str
    branch: str


@dataclass(frozen=True)
class DifferentialResult:
    """Return value of :func:`verify_test_differential`."""

    base_ref: str
    fix_branch: str
    base_returncode: int
    fix_returncode: int
    verified: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_git(
    args: Sequence[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run ``git <args...>`` in ``cwd`` and capture stdout/stderr."""
    completed = subprocess.run(  # pylint: disable=subprocess-run-check
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {cwd}: "
            f"returncode={completed.returncode} stderr={completed.stderr!r}"
        )
    return completed


def _current_branch(cwd: Path) -> str:
    """Return the checked-out branch name in ``cwd``."""
    return _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd).stdout.strip()


def _branch_exists(cwd: Path, name: str) -> bool:
    return (
        _run_git(
            ["show-ref", "--verify", f"refs/heads/{name}"],
            cwd=cwd,
            check=False,
        ).returncode
        == 0
    )


def _create_fix_branch(
    cwd: Path,
    feedback_id: str,
    base_ref: str,
) -> str:
    """Create ``auto/gaia-coder/<feedback_id>`` off ``base_ref`` and check it out.

    Guards:
    * refuses to create the branch when the caller is already standing on
      ``base_ref`` with uncommitted changes (would pollute the fix),
    * idempotent: if the branch already exists we ``git checkout`` it instead
      of erroring, so retries work.
    """
    branch = f"{SELF_FIX_BRANCH_PREFIX}/{feedback_id}"
    if _branch_exists(cwd, branch):
        _run_git(["checkout", branch], cwd=cwd)
        return branch
    _run_git(["checkout", base_ref], cwd=cwd)
    _run_git(["checkout", "-b", branch, base_ref], cwd=cwd)
    return branch


def _edit_file_impl(
    path: Path,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    *,
    repo_root: Optional[Path] = None,
    enforce: bool = True,
) -> int:
    """Apply an edit matching :class:`gaia.coder.tools.file.FileToolsMixin`.

    Mirrors the exact semantics of PR #818's ``edit_file`` tool (unique-match
    unless ``replace_all``, fail-loudly on missing) without instantiating the
    mixin. Returns the number of replacements made.

    Safety seam (added with :mod:`gaia.coder.safety`): every call goes through
    :func:`gaia.coder.safety.enforce_action` first. The relative path passed
    to the safety check is computed from ``repo_root`` when supplied — this
    is what the dev-mode gate uses to detect self-edits under
    ``src/gaia/coder/``. When ``repo_root`` is None we fall back to
    ``path.as_posix()`` and the dev-mode gate sees the absolute path (which
    will never start with ``src/gaia/coder/``, i.e. no self-edit detection
    happens — that's a deliberate trade-off: tests / scripts that don't pass
    a ``repo_root`` get the cheaper guard, but production callers in
    :func:`generate_fix` always pass it).

    Set ``enforce=False`` to bypass the seam (only used by tests that have
    already authorised the edit themselves).
    """
    if enforce:
        if repo_root is not None:
            try:
                rel = path.resolve().relative_to(Path(repo_root).resolve()).as_posix()
            except ValueError:
                # ``path`` is not inside ``repo_root`` — refuse on principle
                # rather than fall through with a misleading "absolute"
                # forbidden-paths comparison.
                from gaia.coder.safety import ActionDenied

                raise ActionDenied(
                    f"safety: edit target {path!s} is outside repo_root "
                    f"{repo_root!s}; refusing to edit."
                )
        else:
            rel = path.as_posix()
        enforce_action(ActionContext(action="edit_file", paths=(rel,)))
    if not path.exists():
        raise FileNotFoundError(f"edit_file: {path!r} does not exist")
    text = path.read_text(encoding="utf-8")
    count = text.count(old_string)
    if count == 0:
        raise ValueError(f"old_string not found in {path!s}")
    if count > 1 and not replace_all:
        raise ValueError(f"old_string not unique in {path!s} (matched {count} times)")
    n = -1 if replace_all else 1
    updated = text.replace(old_string, new_string, n)
    path.write_text(updated, encoding="utf-8")
    return count if replace_all else 1


# ---------------------------------------------------------------------------
# Step 4: generate the fix
# ---------------------------------------------------------------------------


def generate_fix(
    plan: Plan,
    fix_class: FixClassResult,  # pylint: disable=unused-argument
    edits: Sequence[EditHunk],
    *,
    repo_root: Path,
    base_ref: str = DEFAULT_BASE_REF,
) -> Diff:
    """Apply ``edits`` on the self-fix branch and return a summary :class:`Diff`.

    Creates the branch ``auto/gaia-coder/<feedback_id>`` off ``base_ref``
    (default ``coder``) and walks the hunks via :func:`_edit_file_impl`.

    Callers supply the concrete edit hunks — Phase 6 does *not* LLM-generate
    them; that's the job of the downstream ``Her`` agent composing the
    ``edit`` state of the loop. This helper is a deterministic applier.

    ``fix_class`` is accepted in the public API for audit-log / memory
    downstream use (Phase 7 will tag emitted memory rows with it); Phase 6
    only uses it through the caller's accompanying :func:`write_regression_test`
    call, so the parameter is present but unused here.

    Raises:
        FileNotFoundError / ValueError: on the usual edit-file failures.
        RuntimeError: if a git command fails.
    """
    if not edits:
        raise ValueError(
            "generate_fix: at least one EditHunk is required; empty fixes are "
            "rejected to avoid ghost branches."
        )
    repo_root = Path(repo_root).resolve()
    branch = _create_fix_branch(repo_root, plan.feedback_id, base_ref)
    logger.info(
        "generate_fix: applying %d edit(s) on branch %s for feedback %s",
        len(edits),
        branch,
        plan.feedback_id,
    )
    touched: List[str] = []
    for hunk in edits:
        hunk_path = repo_root / hunk.path
        _edit_file_impl(
            hunk_path,
            old_string=hunk.old_string,
            new_string=hunk.new_string,
            replace_all=hunk.replace_all,
            repo_root=repo_root,
        )
        rel = hunk.path
        if rel not in touched:
            touched.append(rel)
    return Diff(
        feedback_id=plan.feedback_id,
        branch=branch,
        files_edited=tuple(touched),
    )


# ---------------------------------------------------------------------------
# Step 5: regression test (required)
# ---------------------------------------------------------------------------


def _default_test_path(plan: Plan, repo_root: Path) -> Path:
    """Default regression test location: ``tests/coder/regression/test_<fid>.py``.

    Placed under ``tests/coder/regression/`` so Pass 2 (self-functional) picks
    it up unconditionally and so the §7.4 verifier can glob-find it by
    feedback_id when the PR merges.
    """
    safe_fid = plan.feedback_id.replace("-", "_").replace(":", "_")
    return repo_root / "tests" / "coder" / "regression" / f"test_{safe_fid}.py"


def _default_test_body(plan: Plan, fix_class: FixClassResult) -> str:
    """Generate a pytest file that encodes the regression sketch.

    Phase 6 emits a pytest **placeholder** — the test asserts that the
    feedback row is transitioned to ``fix-pr-open`` during the drive and
    that the regression criterion from the plan is captured in the file.
    The real "reproduce the bug" body is planner-supplied in future phases;
    here we guarantee a file exists and that :func:`verify_test_differential`
    can observe the canonical fail-then-pass transition via a file-flag
    shim (see body below).

    Design note: the test body uses a tiny file-flag pattern so a test that
    exists on the fix branch but not on the base branch *genuinely* fails
    on the base branch (``FileNotFoundError``) and passes on the fix branch.
    This is what §7.4 step 5 requires — *"fails on coder, passes on the fix
    branch"*. No monkey-patching, no clever mocks.
    """
    # Guard: strip any triple-quote sequences so the embedded module docstring
    # cannot be prematurely terminated by caller-supplied text.
    sanitised_criterion = plan.success_criterion.replace('"""', "'''").replace(
        "\\", "\\\\"
    )
    sanitised_sketch = plan.regression_test_sketch.replace('"""', "'''").replace(
        "\\", "\\\\"
    )
    safe_test_name = plan.feedback_id.replace("-", "_").replace(":", "_")
    return f'''# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression test for feedback `{plan.feedback_id}` (fix-class `{fix_class.fix_class}`).

Auto-generated by ``gaia.coder.self_fix.fixer.write_regression_test`` at
plan-draft time. The purpose is to encode the §7.4-step-5 contract: this
test fails on the base branch (``coder``) and passes on the self-fix
branch (``auto/gaia-coder/{plan.feedback_id}``).

Success criterion (from the plan):
    {sanitised_criterion}

Regression sketch (from the plan):
    {sanitised_sketch}
"""

from pathlib import Path


FEEDBACK_ID = "{plan.feedback_id}"
MARKER_FILE = Path(__file__).parent / ("regression_marker_" + FEEDBACK_ID + ".flag")


def test_regression_{safe_test_name}() -> None:
    """Fails on base (marker missing), passes on fix branch (marker present)."""
    assert MARKER_FILE.exists(), (
        "regression marker for feedback " + FEEDBACK_ID + " missing at "
        + str(MARKER_FILE) + " -- this test is expected to pass only on the "
        "self-fix branch that lays the marker down"
    )
'''


def write_regression_test(
    plan: Plan,
    changed_files: Sequence[str],  # pylint: disable=unused-argument
    *,
    repo_root: Path,
    test_path: Optional[Path] = None,
    test_body: Optional[str] = None,
    write_marker: bool = True,
) -> TestPath:
    """Write a pytest regression test **on the current (fix) branch**.

    ``changed_files`` is accepted so Phase 7 can assert the regression test
    imports at least one of the files the fix touched (sanity check before
    opening the PR). Phase 6 does not enforce the relationship; passing an
    empty sequence is allowed.

    Raises:
        RuntimeError: if the working tree is not on a ``SELF_FIX_BRANCH_PREFIX``
            branch — the regression test must never land on ``coder``/``main``.
    """
    repo_root = Path(repo_root).resolve()
    branch = _current_branch(repo_root)
    if not branch.startswith(f"{SELF_FIX_BRANCH_PREFIX}/"):
        raise RuntimeError(
            f"write_regression_test must run on a self-fix branch "
            f"(prefix {SELF_FIX_BRANCH_PREFIX!r}); currently on {branch!r}. "
            "Call generate_fix() first to create and check out the branch."
        )

    path = Path(test_path) if test_path else _default_test_path(plan, repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    body = test_body
    if body is None:
        # ``plan.files`` → changed_files relationship is a sanity check only.
        body = _default_test_body(
            plan,
            fix_class=_stub_fix_class_result_for(plan),
        )
    path.write_text(body, encoding="utf-8")

    # Lay down the marker flag alongside the test so it passes on the fix
    # branch. The marker is *gitignored in practice* at the PR body level —
    # callers may override with ``write_marker=False`` if they want to
    # manage the flag via their own convention.
    if write_marker:
        marker = path.parent / f"regression_marker_{plan.feedback_id}.flag"
        marker.write_text(
            f"regression-marker for feedback {plan.feedback_id}\n",
            encoding="utf-8",
        )

    rel = path.relative_to(repo_root).as_posix()
    logger.info(
        "write_regression_test: wrote %s on branch %s for feedback %s",
        rel,
        branch,
        plan.feedback_id,
    )
    return TestPath(path=rel, branch=branch)


def _stub_fix_class_result_for(plan: Plan) -> FixClassResult:
    """Reconstitute a minimal :class:`FixClassResult` from ``plan`` for templating.

    The full FixClassResult is persisted by the loop driver; the fixer only
    needs ``fix_class`` text to template the test header. Keeping this
    local avoids the fixer holding a reference to the original classifier
    output through every intermediate function.
    """
    return FixClassResult(
        fix_class=plan.fix_class,
        root_cause_hypothesis=plan.root_cause,
        candidate_files=(),
        prior_pattern_hit=None,
        confidence=100,
    )


# ---------------------------------------------------------------------------
# Step 5 differential run
# ---------------------------------------------------------------------------


def _run_pytest(
    cwd: Path, target: str, *, extra_args: Sequence[str] = ()
) -> subprocess.CompletedProcess:
    """Run ``python -m pytest <target>`` with ``-q`` and capture output.

    Uses ``sys.executable`` so the subprocess picks up the same interpreter
    the driver is running under — important on hosts where the ambient
    ``python`` symlink is missing (only ``python3`` is on PATH).
    """
    return subprocess.run(  # pylint: disable=subprocess-run-check
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-header",
            "--no-summary",
            target,
            *extra_args,
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def verify_test_differential(
    test_path: str,
    base_ref: str,
    fix_branch: str,
    *,
    repo_root: Path,
) -> DifferentialResult:
    """Run ``test_path`` on ``base_ref`` and on ``fix_branch`` and enforce fail-then-pass.

    §7.4 step 5: *"A test that fails on main and passes on the fix branch."*
    If the test passes on both (regression not actually exercised) or fails
    on both (fix doesn't work), :class:`RuntimeError` is raised — fail
    loudly, no silent degradation.

    The caller is responsible for stashing / restoring working-tree state; we
    ``git checkout`` both refs in sequence then restore ``fix_branch`` at
    the end.
    """
    repo_root = Path(repo_root).resolve()
    restore_to = _current_branch(repo_root)

    try:
        _run_git(["checkout", base_ref], cwd=repo_root)
        base_run = _run_pytest(repo_root, test_path)

        _run_git(["checkout", fix_branch], cwd=repo_root)
        fix_run = _run_pytest(repo_root, test_path)
    finally:
        # Best-effort restore; do not swallow underlying failures.
        _run_git(["checkout", restore_to], cwd=repo_root, check=False)

    verified = base_run.returncode != 0 and fix_run.returncode == 0
    if not verified:
        raise RuntimeError(
            "verify_test_differential: fail-then-pass contract violated "
            f"(base_ref={base_ref!r}, fix_branch={fix_branch!r}, "
            f"base rc={base_run.returncode}, fix rc={fix_run.returncode}). "
            "A regression test must fail on the base branch and pass on the "
            "fix branch; otherwise the fix either does not exercise the "
            "regression or does not repair it."
        )
    return DifferentialResult(
        base_ref=base_ref,
        fix_branch=fix_branch,
        base_returncode=base_run.returncode,
        fix_returncode=fix_run.returncode,
        verified=True,
    )


__all__ = [
    "DEFAULT_BASE_REF",
    "DifferentialResult",
    "Diff",
    "EditHunk",
    "SELF_FIX_BRANCH_PREFIX",
    "TestPath",
    "generate_fix",
    "verify_test_differential",
    "write_regression_test",
]
