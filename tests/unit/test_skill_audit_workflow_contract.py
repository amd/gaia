# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The skill-audit workflow is a REQUIRED gate — assert the properties that make it one.

Skills are contributed by pull request (there is no self-serve publish API: the
Hub's `PUBLISH_TOKENS` is a maintainer-held secret), so this workflow is the
primary contributor path rather than a secondary check. That makes several of its
settings load-bearing in ways a future edit could quietly undo — a `paths:` filter
would strand every non-skill PR, and softening REVIEW back to a warning would turn
the gate into a comment-only bot.

These are configuration assertions, not behaviour tests. They exist because the
failure mode is silent: the workflow still runs and still goes green, it just
stops gating anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "skill_audit.yml"

#: The label a maintainer applies to release a REVIEW verdict.
SIGNOFF_LABEL = "skill-audit-reviewed"

#: The check name to add to branch protection. Changing it silently un-requires
#: the gate, because branch protection matches on this exact string.
GATE_JOB_NAME = "Skill Audit (deterministic gate)"


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert WORKFLOW.is_file(), f"{WORKFLOW} is missing"
    # `on:` is parsed by PyYAML 1.1 rules as the boolean True, hence the lookup.
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def triggers(workflow: dict) -> dict:
    return workflow.get("on") or workflow.get(True)


@pytest.fixture(scope="module")
def gate(workflow: dict) -> dict:
    return workflow["jobs"]["skill-audit"]


@pytest.fixture(scope="module")
def enforce_step(gate: dict) -> dict:
    steps = [s for s in gate["steps"] if s.get("name") == "Enforce verdict"]
    assert steps, "the 'Enforce verdict' step is what makes this a gate"
    return steps[0]


# ----------------------------------------------------------------------
# It must be able to report on every PR
# ----------------------------------------------------------------------


def test_the_gate_job_keeps_its_branch_protection_name(gate):
    assert gate["name"] == GATE_JOB_NAME


def test_it_runs_on_pull_requests(triggers):
    assert "pull_request" in triggers


def test_it_has_no_paths_filter(triggers):
    """A required check that never runs blocks the PR forever.

    With a `paths:` filter, GitHub never creates the check run for a PR that
    touches no skill, and branch protection waits on it indefinitely. The job
    runs everywhere and no-ops instead.
    """
    assert "paths" not in (triggers["pull_request"] or {}), (
        "skill_audit.yml must not filter on paths while it is a required check — "
        "every non-skill PR would become unmergeable."
    )
    assert "paths-ignore" not in (triggers["pull_request"] or {})


def test_applying_the_signoff_label_re_runs_the_check(triggers):
    """Otherwise sign-off needs a manual re-run and looks broken."""
    assert "labeled" in triggers["pull_request"]["types"]


def test_it_no_ops_rather_than_failing_when_no_skill_changed(gate):
    """The cost of having no paths filter: it must exit cheaply."""
    conditioned = [s for s in gate["steps"] if "skills != '[]'" in str(s.get("if", ""))]
    assert conditioned, "expensive steps must be gated on a skill having changed"


# ----------------------------------------------------------------------
# It must actually gate
# ----------------------------------------------------------------------


def test_block_fails_the_check(enforce_step):
    run = enforce_step["run"]
    assert "BLOCK_COUNT" in run
    assert "exit 1" in run, "a BLOCK verdict has to fail the job"


def test_an_unparseable_skill_fails_the_check(enforce_step):
    assert "INVALID_COUNT" in enforce_step["run"]


def test_review_is_held_rather_than_warned_through(enforce_step):
    """REVIEW must fail by default. It was previously a passing warning."""
    run = enforce_step["run"]
    assert "REVIEW_COUNT" in run
    assert SIGNOFF_LABEL in run, (
        "REVIEW must be released by an explicit maintainer sign-off label, not "
        "waved through with a warning"
    )
    assert (
        "::warning title=Skill audit::$REVIEW_COUNT" not in run
    ), "REVIEW is no longer advisory — it must fail until signed off"


def test_the_signoff_label_is_read_from_the_pull_request(enforce_step):
    env = enforce_step.get("env", {})
    signed = str(env.get("SIGNED_OFF", ""))
    assert "pull_request.labels" in signed
    assert SIGNOFF_LABEL in signed


def _shell_block(run: str, opening: str) -> str:
    """Return the lines of the `if` block starting at ``opening``.

    Matched by indentation rather than the next ``fi``, because prose inside the
    block ("Fix the findings") contains that substring.
    """
    lines = run.splitlines()
    start = next(i for i, line in enumerate(lines) if opening in line)
    indent = len(lines[start]) - len(lines[start].lstrip())
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        body.append(line)
    return "\n".join(body)


def test_signoff_cannot_override_a_block(enforce_step):
    """Sign-off is for the judgement call, never for a rejected skill.

    Asserted structurally: the BLOCK branch must set the failure flag without
    consulting SIGNED_OFF, so a later edit cannot conflate the two.
    """
    block_branch = _shell_block(enforce_step["run"], 'BLOCK_COUNT:-0}" -gt 0 ')
    assert "failed=1" in block_branch
    assert "SIGNED_OFF" not in block_branch


def test_signoff_cannot_override_an_unparseable_skill(enforce_step):
    invalid_branch = _shell_block(enforce_step["run"], 'INVALID_COUNT:-0}" -gt 0 ')
    assert "failed=1" in invalid_branch
    assert "SIGNED_OFF" not in invalid_branch


def test_only_the_review_branch_consults_the_signoff_label(enforce_step):
    review_branch = _shell_block(enforce_step["run"], 'REVIEW_COUNT:-0}" -gt 0 ')
    assert "SIGNED_OFF" in review_branch
    # Still fails when unsigned.
    assert "failed=1" in review_branch


def test_the_gate_runs_even_on_fork_pull_requests(gate):
    """A fork PR shipping a BLOCK skill must still fail the required check."""
    condition = str(gate.get("if", ""))
    assert "head.repo.full_name" not in condition, (
        "the deterministic gate must not be restricted to same-repo PRs; only "
        "the secret-dependent jobs (SARIF upload, Claude, PR comment) may be"
    )


# ----------------------------------------------------------------------
# Disclosure policy
# ----------------------------------------------------------------------


def test_snippets_are_never_requested_by_an_audit_invocation():
    """Exploitable source text must not reach a run log, artifact, or comment.

    Checked against actual `gaia skill audit` invocations rather than the whole
    file, so the header and the "reproduce it locally with --show-snippets" hint
    can keep mentioning the flag without tripping this.
    """
    offenders = [
        line.strip()
        for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
        if "gaia skill audit" in line and "--show-snippets" in line
        # A backtick means it is prose (a comment, or the "reproduce it locally"
        # hint in the PR comment), not a command this workflow runs.
        and "`gaia skill audit" not in line
    ]
    assert offenders == [], offenders


def test_findings_reach_the_private_channel(workflow):
    sarif_job = workflow["jobs"]["publish-sarif"]
    assert sarif_job["permissions"]["security-events"] == "write"


# ----------------------------------------------------------------------
# The submissions directory
# ----------------------------------------------------------------------


def test_the_community_submissions_directory_exists():
    """Contributors need somewhere to PR into, alongside skills/starter/."""
    readme = REPO_ROOT / "skills" / "community" / "README.md"
    assert readme.is_file(), (
        "skills/community/README.md is the contribution entry point referenced "
        "by the publishing guide"
    )


def test_the_submissions_readme_states_the_pr_route_and_the_tier_rule():
    readme = (REPO_ROOT / "skills" / "community" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "pull request" in readme.lower()
    assert "no self-serve publish API" in readme
    # Merging must never read as a promotion.
    assert "not** a tier promotion" in readme or "not a tier promotion" in readme
