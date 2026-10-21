# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Lemonade bump job must check its own pins before it opens the PR.

The job opens its PR with GITHUB_TOKEN, and GitHub starts no pull_request
workflows for that, so no CI ever ran on a bot-authored bump commit. These are
configuration assertions: the job would still run and still go green if one of
them broke, it would just stop checking anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "lemonade-version-bump.yml"
)


@pytest.fixture(scope="module")
def steps() -> list:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["bump"]["steps"]


def _index(steps, predicate, what):
    found = [i for i, step in enumerate(steps) if predicate(step)]
    assert found, f"lemonade-version-bump.yml has no step that {what}"
    return found[0]


def _runs(step, text):
    return text in (step.get("run") or "")


def _pin_check(step):
    return _runs(step, "TestEveryReleaseIsPinned") and _runs(
        step, "tests/integration/test_lemonade_embeddable_assets.py"
    )


def test_the_pins_are_checked_strictly_on_the_bump_commit(steps):
    step = steps[_index(steps, _pin_check, "runs the pin tests")]
    env = step.get("env") or {}
    assert (
        str(env.get("GAIA_REQUIRE_RELEASE_API")) == "1"
    ), "Without GAIA_REQUIRE_RELEASE_API a rate limit skips every pin test green"
    assert env.get("GITHUB_TOKEN"), "an unauthenticated API call is rate-limited"
    assert "pytest" in step["run"]
    assert "||" not in step["run"], "the pin check must be able to fail the job"
    assert step.get("continue-on-error", False) is False


def test_gaia_is_installed_before_the_pin_check(steps):
    install = _index(
        steps,
        lambda s: _runs(s, "pip install") and _runs(s, "-e ."),
        "installs GAIA",
    )
    assert install < _index(steps, _pin_check, "runs the pin tests")


def test_the_pr_is_opened_only_after_the_pins_pass(steps):
    check = _index(steps, _pin_check, "runs the pin tests")
    open_pr = _index(steps, lambda s: _runs(s, "gh pr create"), "opens the PR")
    assert check < open_pr
    pin_id = steps[check].get("id")
    assert pin_id, "the pin check needs an id for the PR step to depend on"
    assert f"steps.{pin_id}.outcome == 'success'" in steps[open_pr].get("if", "")
    push = _index(steps, lambda s: _runs(s, "git push"), "pushes the branch")
    assert check < push


def test_the_agent_does_not_open_the_pr_itself(steps):
    agent = _index(
        steps,
        lambda s: "claude-code-action" in (s.get("uses") or ""),
        "runs the bump agent",
    )
    prompt = steps[agent]["with"]["prompt"]
    for command in ("gh pr create", "gh pr edit", "git push"):
        assert (
            command not in prompt
        ), f"the agent's prompt still runs `{command}`, which skips the pin check"


def test_a_failed_pin_check_files_the_tracking_issue(steps):
    check = steps[_index(steps, _pin_check, "runs the pin tests")]
    issue = steps[
        _index(steps, lambda s: _runs(s, "gh issue create"), "files the issue")
    ]
    assert "failure()" in issue.get("if", "")
    pins_env = [k for k, v in issue["env"].items() if f"steps.{check['id']}" in str(v)]
    assert pins_env, "the tracking issue cannot tell a failed pin check apart"
    assert f'"${pins_env[0]}" = "failure"' in issue["run"]
    assert "no PR was opened" in issue["run"]


def test_the_pin_check_tests_the_commit_that_is_pushed(steps):
    find = steps[
        _index(steps, lambda s: _runs(s, "git rev-parse --verify"), "finds the commit")
    ]
    assert "git status --porcelain" in find["run"], "leftover edits would be tested"
    assert "git rev-list --count" in find["run"]
    check = steps[_index(steps, _pin_check, "runs the pin tests")]
    assert f"steps.{find['id']}.outputs.ready == 'true'" in check.get("if", "")
    assert "check_doc_versions.py" in check["run"]
    assert "set -euo pipefail" in check["run"]
