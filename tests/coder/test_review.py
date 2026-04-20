# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for the seven-pass self-review gate (Phase 4; §8 + §15.8).

No real Anthropic calls are made. Every LLM call is patched at the
:func:`gaia.coder.review._llm.call_opus` seam — the cheapest and most
stable patch point per the module docstring.

Subprocess calls are not mocked globally — the deterministic passes
*should* exercise real ``subprocess.run`` so a regression in how we
invoke tools surfaces here rather than in the integration suite. A
handful of targeted ``mocker.patch`` calls stub out specific commands
(``gitleaks``, ``pip-audit``) when a test needs a particular outcome.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest
import pytest_mock  # noqa: F401 — used in the `patch_opus` fixture type hint

from gaia.coder.review import (
    ReviewToolsMixin,
    make_pass_result,
    run_all_passes,
)
from gaia.coder.review._diff import DiffBundle

# ---------------------------------------------------------------------------
# Shared fixtures — fabricated diff bundles for the passes to chew on.
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_diff() -> DiffBundle:
    """A tidy diff: small .py change, no debug prints, no TODOs."""
    return DiffBundle(
        source="branch",
        identifier="feature/tidy",
        unified_diff=(
            "diff --git a/src/example.py b/src/example.py\n"
            "index abc..def 100644\n"
            "--- a/src/example.py\n"
            "+++ b/src/example.py\n"
            "@@ -1,2 +1,3 @@\n"
            " def add(a, b):\n"
            "-    return a + b\n"
            "+    # Compute the sum of two numbers.\n"
            "+    return a + b\n"
        ),
        changed_files=["src/example.py"],
        pr_title="feat(example): annotate add()",
        pr_body=(
            "## Summary\n"
            "Adds a one-line docstring to `add()` so future readers know "
            "why this exists — without the comment the function is "
            "indistinguishable from a stray utility.\n\n"
            "## Test plan\n"
            "- [x] pytest tests/unit/test_example.py\n"
        ),
        base_ref="coder",
    )


@pytest.fixture
def dirty_diff() -> DiffBundle:
    """A diff with a debug print, a bare TODO, and forbidden attribution."""
    return DiffBundle(
        source="branch",
        identifier="feature/messy",
        unified_diff=(
            "diff --git a/src/bad.py b/src/bad.py\n"
            "index abc..def 100644\n"
            "--- a/src/bad.py\n"
            "+++ b/src/bad.py\n"
            "@@ -1,2 +1,6 @@\n"
            " def add(a, b):\n"
            "+    print('debugging')\n"
            "+    # TODO remember to fix this someday\n"
            "+    # old_value = 42\n"
            "     return a + b\n"
        ),
        changed_files=["src/bad.py"],
        pr_title="updated stuff",  # not conventional commits
        pr_body=(
            "some thing i did\n\n"
            "Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>\n"
        ),
        base_ref="coder",
    )


@pytest.fixture
def patch_opus(mocker: pytest_mock.MockerFixture):
    """Return a helper that patches ``call_opus`` with a canned JSON body.

    Usage::

        patch_opus({"overall": "pass", "rules": [], "blockers": []})
        result = pass_3_architectural.run_pass(...)
    """

    def _patch(payload: Dict[str, Any], *, target: str = "all") -> List[Any]:
        """Patch call_opus in one or all review-pass modules.

        ``target`` may be ``"all"`` or one of ``"3"``/``"5"``/``"6"``/``"7"``.
        We patch the *imported* name inside each pass module — patching
        the source module alone does not catch the already-bound names.
        """
        body = json.dumps(payload)
        mocks = []
        module_targets = {
            "3": "gaia.coder.review.pass_3_architectural.call_opus",
            "5": "gaia.coder.review.pass_5_prose.call_opus",
            "6": "gaia.coder.review.pass_6_adversarial.call_opus",
            "7": "gaia.coder.review.pass_7_feedback_binding.call_opus",
        }
        if target == "all":
            for path in module_targets.values():
                mocks.append(mocker.patch(path, return_value=body))
        else:
            mocks.append(mocker.patch(module_targets[target], return_value=body))
        return mocks

    return _patch


# ---------------------------------------------------------------------------
# Pass-level tests
# ---------------------------------------------------------------------------


class TestPass1Static:
    def test_pass_on_clean_diff(self, clean_diff, mocker):
        from gaia.coder.review import pass_1_static

        # Stub out the lint subprocess — lint passes.
        mocker.patch(
            "gaia.coder.review.pass_1_static._run",
            return_value=(0, "", ""),
        )
        result = pass_1_static.run_pass("feature/tidy", diff=clean_diff)
        assert result["status"] == "pass"
        assert result["confidence"] is None
        assert "docs/plans/coder-agent.mdx §8 Pass 1" in result["citations"]

    def test_fail_on_dirty_diff(self, dirty_diff, mocker):
        from gaia.coder.review import pass_1_static

        # Lint passes so the *only* failure signal is the regex guards.
        mocker.patch(
            "gaia.coder.review.pass_1_static._run",
            return_value=(0, "", ""),
        )
        result = pass_1_static.run_pass("feature/messy", diff=dirty_diff)
        descriptions = [f["description"] for f in result["findings"]]
        # Regex guards are "minor" so status stays "pass" unless lint hard
        # -fails — this asserts the regex still *surfaced* the issues.
        assert any("debug print" in d for d in descriptions)
        assert any("TODO" in d for d in descriptions)

    def test_fail_when_lint_subprocess_fails(self, clean_diff, mocker):
        from gaia.coder.review import pass_1_static

        mocker.patch(
            "gaia.coder.review.pass_1_static._run",
            return_value=(1, "", "lint failed: 3 errors"),
        )
        result = pass_1_static.run_pass("feature/tidy", diff=clean_diff)
        assert result["status"] == "fail"
        assert any(f["severity"] == "blocking" for f in result["findings"])


class TestPass2Functional:
    def test_pass_when_pytest_ok(self, clean_diff, mocker):
        from gaia.coder.review import pass_2_functional

        mocker.patch(
            "gaia.coder.review.pass_2_functional._run",
            return_value=(0, "TOTAL 100 0 87%\n", ""),
        )
        mocker.patch(
            "gaia.coder.review.pass_2_functional.shutil.which",
            side_effect=lambda name: "/usr/bin/pytest" if name == "pytest" else None,
        )
        result = pass_2_functional.run_pass("feature/tidy", diff=clean_diff)
        assert result["status"] == "pass"
        assert result["confidence"] == 87

    def test_fail_when_pytest_fails(self, clean_diff, mocker):
        from gaia.coder.review import pass_2_functional

        mocker.patch(
            "gaia.coder.review.pass_2_functional._run",
            return_value=(1, "", "FAILED tests/unit/test_add.py::test_add"),
        )
        mocker.patch(
            "gaia.coder.review.pass_2_functional.shutil.which",
            side_effect=lambda name: "/usr/bin/pytest" if name == "pytest" else None,
        )
        result = pass_2_functional.run_pass("feature/tidy", diff=clean_diff)
        assert result["status"] == "fail"
        assert any("pytest failed" in f["description"] for f in result["findings"])


class TestPass3Architectural:
    def test_pass_when_opus_says_pass(self, clean_diff, patch_opus):
        from gaia.coder.review import pass_3_architectural

        patch_opus(
            {
                "rules": [{"rule": "LAYERING", "verdict": "pass", "citation": "N/A"}],
                "overall": "pass",
                "blockers": [],
            },
            target="3",
        )
        result = pass_3_architectural.run_pass("feature/tidy", diff=clean_diff)
        assert result["status"] == "pass"

    def test_fail_when_opus_says_request_changes(self, clean_diff, patch_opus):
        from gaia.coder.review import pass_3_architectural

        patch_opus(
            {
                "rules": [
                    {"rule": "LAYERING", "verdict": "fail", "citation": "src/x.py:10"}
                ],
                "overall": "request-changes",
                "blockers": ["new cycle between x and y"],
            },
            target="3",
        )
        result = pass_3_architectural.run_pass("feature/tidy", diff=clean_diff)
        assert result["status"] == "fail"
        assert any("cycle" in f["description"] for f in result["findings"])


class TestPass4Security:
    def test_pass_when_nothing_dangerous(self, clean_diff, mocker, tmp_path):
        from gaia.coder.review import pass_4_security

        mocker.patch(
            "gaia.coder.review.pass_4_security.shutil.which",
            return_value=None,  # no gitleaks, no pip-audit, no npm
        )
        # No .py files actually exist — pass_4 safely returns no AST hits.
        result = pass_4_security.run_pass(
            "feature/tidy", diff=clean_diff, repo_root=tmp_path
        )
        assert result["status"] == "pass"

    def test_fail_when_eval_on_added_code(self, clean_diff, tmp_path, mocker):
        from gaia.coder.review import pass_4_security

        mocker.patch(
            "gaia.coder.review.pass_4_security.shutil.which",
            return_value=None,
        )
        bad_py = tmp_path / "src" / "bad.py"
        bad_py.parent.mkdir(parents=True)
        bad_py.write_text("def run(x):\n    return eval(x)\n")
        diff = DiffBundle(
            source="branch",
            identifier="feature/danger",
            unified_diff=(
                "diff --git a/src/bad.py b/src/bad.py\n"
                "+def run(x):\n+    return eval(x)\n"
            ),
            changed_files=["src/bad.py"],
            pr_title="feat: add runner",
            pr_body="## Summary\n\n## Test plan\n",
            base_ref="coder",
        )
        result = pass_4_security.run_pass(
            "feature/danger", diff=diff, repo_root=tmp_path
        )
        assert result["status"] == "fail"
        assert any("eval" in f["description"] for f in result["findings"])


class TestPass5Prose:
    def test_pass_when_regex_clean_and_llm_clean(self, clean_diff, patch_opus):
        from gaia.coder.review import pass_5_prose

        patch_opus(
            {"violations": [], "verdict": "pass", "reasoning": "clean"},
            target="5",
        )
        result = pass_5_prose.run_pass("feature/tidy", diff=clean_diff)
        assert result["status"] == "pass"

    def test_fail_on_claude_attribution(self, dirty_diff):
        from gaia.coder.review import pass_5_prose

        # No LLM patch needed — stage-1 regex short-circuits on the
        # Co-Authored-By trailer before we call Opus.
        result = pass_5_prose.run_pass("feature/messy", diff=dirty_diff, skip_llm=True)
        assert result["status"] == "fail"
        assert any(
            "attribution" in f["description"].lower() for f in result["findings"]
        )


class TestPass6Adversarial:
    def test_pass_when_confidence_high(self, clean_diff, patch_opus):
        from gaia.coder.review import pass_6_adversarial

        patch_opus(
            {
                "findings": [
                    {
                        "file_line": "src/example.py:3",
                        "severity": "minor",
                        "description": "docstring could reference add arity",
                        "fix": "clarify",
                    }
                ],
                "confidence_score": 92,
                "rubric_reasoning": "tight match to criterion",
            },
            target="6",
        )
        result = pass_6_adversarial.run_pass("feature/tidy", diff=clean_diff)
        assert result["status"] == "pass"
        assert result["confidence"] == 92

    def test_fail_when_confidence_low(self, clean_diff, patch_opus):
        from gaia.coder.review import pass_6_adversarial

        patch_opus(
            {
                "findings": [],
                "confidence_score": 42,
                "rubric_reasoning": "unrelated",
            },
            target="6",
        )
        result = pass_6_adversarial.run_pass("feature/tidy", diff=clean_diff)
        assert result["status"] == "fail"
        assert result["confidence"] == 42


class TestPass7FeedbackBinding:
    def test_skipped_without_feedback_id(self, clean_diff):
        from gaia.coder.review import pass_7_feedback_binding

        # clean_diff's pr_body has no Feedback-Id.
        result = pass_7_feedback_binding.run_pass("feature/tidy", diff=clean_diff)
        assert result["status"] == "skipped"

    def test_pass_with_feedback_id_and_clean_llm(self, patch_opus):
        from gaia.coder.review import pass_7_feedback_binding

        diff = DiffBundle(
            source="pr",
            identifier="https://github.com/amd/gaia/pull/999",
            unified_diff="diff\n",
            changed_files=["src/example.py"],
            pr_title="fix(example): address feedback",
            pr_body=(
                "## Summary\nAddresses EM feedback.\n"
                "Feedback-Id: 9f8e7d6c\n"
                "regression_test: tests/coder/test_example.py\n"
            ),
            base_ref="coder",
        )
        patch_opus(
            {
                "checks": [
                    {
                        "name": "regression_test",
                        "verdict": "pass",
                        "evidence": "differential pytest confirmed",
                    }
                ],
                "overall": "pass",
                "blockers": [],
            },
            target="7",
        )
        result = pass_7_feedback_binding.run_pass(
            "feature/selffix",
            diff=diff,
            skip_differential_pytest=True,
        )
        assert result["status"] == "pass"


# ---------------------------------------------------------------------------
# Gate-level tests
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_all_passes(mocker, clean_diff):
    """Patch every pass's ``run_pass`` to return a canned result.

    Returns a helper ``set_status(pass_index, status, **kwargs)`` that
    tests can use to mark individual passes pass/fail/skipped.
    """
    # Also stub resolve_diff so the gate does not shell to git.
    mocker.patch("gaia.coder.review.gate.resolve_diff", return_value=clean_diff)

    module_names = {
        1: "pass_1_static",
        2: "pass_2_functional",
        3: "pass_3_architectural",
        4: "pass_4_security",
        5: "pass_5_prose",
        6: "pass_6_adversarial",
        7: "pass_7_feedback_binding",
    }
    calls = {}

    def _make_result(idx, status, **extras):
        return make_pass_result(
            status=status,
            findings=extras.get("findings", []),
            confidence=extras.get("confidence"),
            citations=[f"stub-pass-{idx}"],
            tooling_used=[f"stub-{idx}"],
        )

    def _install():
        for idx, name in module_names.items():
            calls[idx] = mocker.patch(
                f"gaia.coder.review.gate.{name}.run_pass",
                return_value=_make_result(idx, "pass"),
            )

    def _set_status(idx, status, **extras):
        calls[idx].return_value = _make_result(idx, status, **extras)

    _install()
    return _set_status, calls


def test_gate_short_circuits_on_hard_fail(stub_all_passes):
    set_status, calls = stub_all_passes
    set_status(
        1,
        "fail",
        findings=[{"severity": "blocking", "description": "lint failed"}],
    )
    result = run_all_passes("feature/x", is_self_fix=False, base_ref="coder")
    # Gate returns "block" on a hard-fail in Passes 1/2/4.
    assert result.overall == "block"
    # Pass 1 ran; Passes 2-7 did not.
    assert calls[1].call_count == 1
    for idx in (2, 3, 4, 5, 6, 7):
        assert calls[idx].call_count == 0
    # The pass_results list is still length 7 with skipped placeholders.
    assert len(result.pass_results) == 7
    assert result.pass_results[0]["status"] == "fail"
    for idx in range(1, 7):
        assert result.pass_results[idx]["status"] == "skipped"


def test_pass_7_runs_only_for_self_fix(stub_all_passes):
    _set_status, calls = stub_all_passes
    # Default: everything passes. is_self_fix=False → Pass 7 is skipped.
    result = run_all_passes("feature/x", is_self_fix=False, base_ref="coder")
    assert result.pass_results[6]["status"] == "skipped"
    assert calls[7].call_count == 0

    # Now flip is_self_fix=True and confirm Pass 7 is actually invoked.
    # (Reset everything; use a fresh gate call.)
    result = run_all_passes("feature/selffix", is_self_fix=True, base_ref="coder")
    assert calls[7].call_count == 1


def test_gate_pass_when_all_pass(stub_all_passes):
    _set_status, _ = stub_all_passes
    # stub_all_passes defaults every pass to "pass".
    result = run_all_passes("feature/x", is_self_fix=False, base_ref="coder")
    assert result.overall == "pass"


def test_gate_request_changes_when_non_hardfail_pass_fails(stub_all_passes):
    set_status, _ = stub_all_passes
    # Pass 3 is LLM-driven and is NOT in HARD_FAIL_PASSES (1, 2, 4).
    # A fail there should surface as "request-changes", not "block".
    set_status(
        3,
        "fail",
        findings=[{"severity": "blocking", "description": "layering violated"}],
    )
    result = run_all_passes("feature/x", is_self_fix=False, base_ref="coder")
    assert result.overall == "request-changes"


def test_pass_6_emits_confidence_score_integer(clean_diff, patch_opus):
    """§7.6 auto-merge gate needs an integer confidence from Pass 6."""
    from gaia.coder.review import pass_6_adversarial

    patch_opus(
        {
            "findings": [],
            "confidence_score": 88,
            "rubric_reasoning": "minor gaps",
        },
        target="6",
    )
    result = pass_6_adversarial.run_pass("feature/tidy", diff=clean_diff)
    assert isinstance(result["confidence"], int)
    assert 0 <= result["confidence"] <= 100
    assert result["confidence"] == 88


# ---------------------------------------------------------------------------
# Prompt-file existence
# ---------------------------------------------------------------------------


def test_prompt_files_exist():
    """§15.8 says every LLM call site has a canonical prompt on disk."""
    prompts_dir = (
        Path(__file__).resolve().parents[2] / "src" / "gaia" / "coder" / "prompts"
    )
    expected = {
        "architectural.md",
        "persona_linter.md",
        "adversarial.md",
        "feedback_binding.md",
    }
    for name in expected:
        path = prompts_dir / name
        assert path.exists(), f"missing prompt: {path}"
        assert path.read_text(encoding="utf-8").strip(), f"prompt {name} is empty"


# ---------------------------------------------------------------------------
# Mixin registration smoke test
# ---------------------------------------------------------------------------


def test_mixin_registers_eight_tools():
    from gaia.agents.base.tools import _TOOL_REGISTRY

    before = set(_TOOL_REGISTRY.keys())
    m = ReviewToolsMixin()
    m.register_review_tools()
    new = set(_TOOL_REGISTRY.keys()) - before
    expected = {
        "review_diff_self_static",
        "review_diff_self_functional",
        "review_diff_self_architectural",
        "review_diff_self_security",
        "review_diff_self_prose",
        "review_diff_self_adversarial",
        "review_diff_self_feedback_binding",
        "review_diff_gate",
    }
    assert expected.issubset(new), f"missing: {expected - new}"
    assert len(new) >= 8
