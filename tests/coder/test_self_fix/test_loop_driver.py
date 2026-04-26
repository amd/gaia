# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""End-to-end tests for ``gaia.coder.self_fix.loop_driver.FeedbackLoopDriver``."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, Sequence

import pytest

from gaia.coder.self_fix import (
    FeedbackLoopDriver,
    LoopDriverConfig,
)
from gaia.coder.self_fix.fixer import EditHunk
from gaia.coder.self_fix.loop_driver import (
    _parse_edit_hunks_response,
    _render_edit_hunks_prompt,
)
from gaia.coder.stores import feedback as feedback_store


def _triage_client(fix_class: str = "tool", confidence: int = 85) -> Callable[..., str]:
    """Factory: triage LLM stub returning a canned classification."""

    def client(**_kwargs):
        return json.dumps(
            {
                "fix_class": fix_class,
                "root_cause_hypothesis": "mock root cause",
                "candidate_files": [
                    {"path": "src/gaia/coder/sample.py", "why": "seeded"}
                ],
                "prior_pattern_hit": None,
                "confidence": confidence,
            }
        )

    return client


def _gh_runner_factory(
    pr_number: int = 123,
) -> Callable[..., subprocess.CompletedProcess]:
    """Factory: gh CLI stub that returns a canned PR URL."""

    def runner(args, cwd=None, check=True):
        if args and args[0] == "pr" and args[1] == "create":
            return subprocess.CompletedProcess(
                args=["gh", *args],
                returncode=0,
                stdout=f"https://github.com/amd/gaia/pull/{pr_number}\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=["gh", *args], returncode=0, stdout="", stderr=""
        )

    return runner


def _stub_edit_hunk_planner(
    new_string_suffix: str = "\n# self-fix marker",
) -> Callable[..., Sequence[EditHunk]]:
    """Factory: deterministic stub planner used by the end-to-end tests.

    Replaces the production LLM-driven ``_default_edit_hunks`` so the e2e
    tests keep exercising the rest of the pipeline without spinning up a
    real Anthropic client.
    """

    def planner(*, plan, fix_class, hits) -> Sequence[EditHunk]:  # noqa: ARG001
        if not hits:
            return []
        first = hits[0]
        return [
            EditHunk(
                path=first.path,
                old_string=first.snippet,
                new_string=first.snippet + new_string_suffix,
                replace_all=False,
            )
        ]

    return planner


def test_loop_driver_end_to_end(
    tmp_git_repo: Path,
    feedback_db_path: Path,
    memory_db_path: Path,
    seed_feedback,
) -> None:
    """Seed a pending feedback row, run the driver, assert fix-pr-open."""
    fid = seed_feedback(
        body="classify_failure misfires on timestamped errors; please fix cache key",
    )
    driver = FeedbackLoopDriver(
        LoopDriverConfig(
            repo_root=tmp_git_repo,
            feedback_db_path=feedback_db_path,
            memory_db_path=memory_db_path,
            em_config={"em_handle": "test-em"},
            base_ref="coder",
        ),
        triage_client=_triage_client(),
        edit_hunk_planner=_stub_edit_hunk_planner(),
        gh_runner=_gh_runner_factory(pr_number=777),
        # The synthetic tmp repo has no pytest collector under it; skipping
        # differential verify keeps the test focussed on state transitions.
        skip_differential_verify=True,
    )
    result = driver.process_pending_feedback()
    assert result.final_state == "fix-pr-open"
    assert result.pr is not None
    assert result.pr.number == 777
    assert result.regression_test_path is not None
    assert result.plan is not None
    assert result.fix_class is not None
    assert result.fix_class.fix_class == "tool"
    # Feedback row should now show fix-pr-open with the PR URL recorded.
    conn = feedback_store.open_store(feedback_db_path)
    try:
        row = feedback_store.get_row(conn, fid)
    finally:
        conn.close()
    assert row is not None
    assert row.state == "fix-pr-open"
    assert row.fix_pr_url == "https://github.com/amd/gaia/pull/777"
    assert row.regression_test_path == result.regression_test_path
    notes = json.loads(row.notes_json)
    transitions = {n.get("transition") for n in notes}
    assert "pending → triaged" in transitions
    assert "triaged → in-fix" in transitions
    assert "in-fix → fix-pr-open" in transitions


def test_loop_driver_rejects_out_of_scope(
    tmp_git_repo: Path,
    feedback_db_path: Path,
    memory_db_path: Path,
    seed_feedback,
) -> None:
    """A low-confidence triage transitions the row to 'rejected' and stops."""
    fid = seed_feedback(body="vague complaint — can't tell what's broken")
    driver = FeedbackLoopDriver(
        LoopDriverConfig(
            repo_root=tmp_git_repo,
            feedback_db_path=feedback_db_path,
            memory_db_path=memory_db_path,
            em_config={"em_handle": "test-em"},
        ),
        # confidence=20 → out-of-scope escalation.
        triage_client=_triage_client(fix_class="tool", confidence=20),
        gh_runner=_gh_runner_factory(),
        skip_differential_verify=True,
    )
    result = driver.process_pending_feedback()
    assert result.final_state == "rejected"
    assert result.pr is None
    conn = feedback_store.open_store(feedback_db_path)
    try:
        row = feedback_store.get_row(conn, fid)
    finally:
        conn.close()
    assert row is not None
    assert row.state == "rejected"


def test_loop_driver_no_pending(
    tmp_git_repo: Path,
    feedback_db_path: Path,
    memory_db_path: Path,
) -> None:
    """Empty feedback queue returns 'no-pending' cleanly (no side effects)."""
    driver = FeedbackLoopDriver(
        LoopDriverConfig(
            repo_root=tmp_git_repo,
            feedback_db_path=feedback_db_path,
            memory_db_path=memory_db_path,
        ),
        triage_client=_triage_client(),
        gh_runner=_gh_runner_factory(),
    )
    result = driver.process_pending_feedback()
    assert result.final_state == "no-pending"
    assert result.feedback_id is None


# ---------------------------------------------------------------------------
# Edit-hunks default planner — LLM-driven path
# ---------------------------------------------------------------------------


def _make_plan_and_hits():
    """Build a tiny Plan + LocalisationHit pair the planner can chew on."""
    from gaia.coder.self_fix.planner import (
        CostEstimate,
        FileTouchPlan,
        Plan,
    )
    from gaia.coder.self_fix.triage import FixClassResult, LocalisationHit

    plan = Plan(
        feedback_id="fb-eh1",
        fix_class="tool",
        root_cause="cache key collides on identical timestamps",
        proposed_change="namespace the cache key by tool_name + payload hash",
        regression_test_sketch="add pytest covering colliding timestamps",
        files=(FileTouchPlan(path="src/gaia/coder/sample.py", loc_estimate=4),),
        alternatives_considered=("rebuild cache",),
        risks=("downstream callers depend on the old key shape",),
        success_criterion="feedback fb-eh1 marked verified",
        cost_estimate=CostEstimate(tokens=12000, usd=0.30, wall_clock_minutes=2.0),
    )
    fix_class = FixClassResult(
        fix_class="tool",
        root_cause_hypothesis="cache key collision",
        candidate_files=(),
        prior_pattern_hit=None,
        confidence=85,
    )
    hits = (
        LocalisationHit(
            path="src/gaia/coder/sample.py",
            line_start=3,
            line_end=3,
            snippet="    return err",
        ),
    )
    return plan, fix_class, hits


def test_default_edit_hunks_routes_through_coder_llm(mocker, tmp_path: Path) -> None:
    """:meth:`_default_edit_hunks` MUST go through ``CoderLLM.complete``.

    The planner mocks the shared ``default_completion_client`` seam, lets
    the driver render the prompt, and asserts the returned EditHunk list
    matches what the LLM "said".
    """
    plan, fix_class, hits = _make_plan_and_hits()

    expected_payload = {
        "edits": [
            {
                "path": "src/gaia/coder/sample.py",
                "old_string": "    return err",
                "new_string": "    return _safe_lookup(err)",
                "replace_all": False,
            }
        ]
    }
    # ``_default_edit_hunks`` lazy-imports from :mod:`gaia.coder.llm`, so
    # we patch the source module — the import inside the function then
    # resolves to the mock.
    fake = mocker.patch(
        "gaia.coder.llm.default_completion_client",
        return_value=json.dumps(expected_payload),
    )
    driver = FeedbackLoopDriver(
        LoopDriverConfig(
            repo_root=tmp_path,
            feedback_db_path=tmp_path / "f.db",
            memory_db_path=tmp_path / "m.db",
        ),
    )
    result = list(driver._default_edit_hunks(plan=plan, fix_class=fix_class, hits=hits))
    assert len(result) == 1
    assert result[0].path == "src/gaia/coder/sample.py"
    assert result[0].new_string == "    return _safe_lookup(err)"
    # Mock was called once with the rendered prompt (containing the slot
    # values from our plan + hits).
    assert fake.call_count == 1
    rendered_prompt = fake.call_args.kwargs["prompt"]
    assert "fb-eh1" in rendered_prompt
    assert "cache key collides on identical timestamps" in rendered_prompt
    assert "src/gaia/coder/sample.py:3-3" in rendered_prompt


def test_default_edit_hunks_raises_on_empty_hits(tmp_path: Path) -> None:
    """No hits = no fix; the default refuses to fabricate one."""
    plan, fix_class, _ = _make_plan_and_hits()
    driver = FeedbackLoopDriver(
        LoopDriverConfig(
            repo_root=tmp_path,
            feedback_db_path=tmp_path / "f.db",
            memory_db_path=tmp_path / "m.db",
        ),
    )
    with pytest.raises(RuntimeError, match="no localised hits"):
        driver._default_edit_hunks(plan=plan, fix_class=fix_class, hits=())


def test_render_edit_hunks_prompt_contains_all_slots() -> None:
    """The rendered prompt must include every plan/hit slot verbatim."""
    plan, fix_class, hits = _make_plan_and_hits()
    rendered = _render_edit_hunks_prompt(plan=plan, fix_class=fix_class, hits=hits)
    assert "fb-eh1" in rendered
    assert "namespace the cache key" in rendered
    assert "feedback fb-eh1 marked verified" in rendered
    assert "src/gaia/coder/sample.py:3-3" in rendered
    assert "    return err" in rendered
    # No raw {{...}} placeholders should leak through to the LLM.
    assert "{{" not in rendered


def test_parse_edit_hunks_response_happy_path() -> None:
    raw = json.dumps(
        {
            "edits": [
                {
                    "path": "src/x.py",
                    "old_string": "old",
                    "new_string": "new",
                    "replace_all": False,
                }
            ]
        }
    )
    edits = _parse_edit_hunks_response(raw, allowed_paths={"src/x.py"})
    assert edits == [
        EditHunk(path="src/x.py", old_string="old", new_string="new", replace_all=False)
    ]


def test_parse_edit_hunks_response_rejects_invalid_json() -> None:
    """Malformed JSON must raise with the first 500 chars embedded."""
    raw = "not-json-at-all" + "x" * 600  # > 500 to prove we truncate
    with pytest.raises(RuntimeError) as excinfo:
        _parse_edit_hunks_response(raw, allowed_paths={"src/x.py"})
    msg = str(excinfo.value)
    assert "not valid JSON" in msg
    # Truncated to first 500 characters.
    assert "x" * 500 in msg or "not-json-at-all" in msg
    assert "x" * 600 not in msg


def test_parse_edit_hunks_response_rejects_invented_path() -> None:
    """Paths outside ``allowed_paths`` must be refused (no file invention)."""
    raw = json.dumps(
        {
            "edits": [
                {
                    "path": "src/never/seen.py",
                    "old_string": "x",
                    "new_string": "y",
                }
            ]
        }
    )
    with pytest.raises(RuntimeError, match="not allowed to invent files"):
        _parse_edit_hunks_response(raw, allowed_paths={"src/x.py"})


def test_parse_edit_hunks_response_rejects_empty_old_string() -> None:
    raw = json.dumps(
        {
            "edits": [
                {"path": "src/x.py", "old_string": "", "new_string": "y"},
            ]
        }
    )
    with pytest.raises(RuntimeError, match="old_string must be a non-empty"):
        _parse_edit_hunks_response(raw, allowed_paths={"src/x.py"})


def test_parse_edit_hunks_response_accepts_empty_edits() -> None:
    """An explicit empty edits list is legal — caller decides what to do."""
    raw = json.dumps({"edits": []})
    assert _parse_edit_hunks_response(raw, allowed_paths={"src/x.py"}) == []
