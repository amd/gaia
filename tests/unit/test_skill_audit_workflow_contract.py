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
    block_branch = _shell_block(enforce_step["run"], 'BLOCK_COUNT}" -gt 0 ')
    assert "failed=1" in block_branch
    assert "SIGNED_OFF" not in block_branch


def test_signoff_cannot_override_an_unparseable_skill(enforce_step):
    invalid_branch = _shell_block(enforce_step["run"], 'INVALID_COUNT}" -gt 0 ')
    assert "failed=1" in invalid_branch
    assert "SIGNED_OFF" not in invalid_branch


def test_only_the_review_branch_consults_the_signoff_label(enforce_step):
    review_branch = _shell_block(enforce_step["run"], '"${REVIEW_COUNT}" -gt 0 ')
    assert "SIGNED_OFF" in review_branch
    # Still fails when unsigned.
    assert "failed=1" in review_branch


def _audit_step(gate: dict) -> dict:
    return next(s for s in gate["steps"] if s.get("name") == "Audit each changed skill")


def test_a_hub_skill_review_is_counted_apart_from_other_reviews(gate):
    """Release publishing accepts only ALLOW, so a hub/skills/ REVIEW can't share
    the label-releasable REVIEW counter."""
    run = _audit_step(gate)["run"]
    assert "hub/skills/*) hub_review=$((hub_review + 1))" in run
    assert 'echo "hub_review_count=$hub_review"' in run
    assert "hub_review_count" in gate["outputs"]


def test_signoff_cannot_override_a_hub_skill_review(enforce_step):
    """A merged hub/skills/ REVIEW skill would fail at release, not at review."""
    env = enforce_step.get("env", {})
    assert "hub_review_count" in str(env.get("HUB_REVIEW_COUNT", ""))
    hub_branch = _shell_block(enforce_step["run"], 'HUB_REVIEW_COUNT}" -gt 0 ')
    assert "failed=1" in hub_branch
    assert "SIGNED_OFF" not in hub_branch


def test_the_gate_runs_even_on_fork_pull_requests(gate):
    """A fork PR shipping a BLOCK skill must still fail the required check."""
    condition = str(gate.get("if", ""))
    assert "head.repo.full_name" not in condition, (
        "the deterministic gate must not be restricted to same-repo PRs; only "
        "the secret-dependent jobs (SARIF upload, Claude, PR comment) may be"
    )


# ----------------------------------------------------------------------
# An audit that never ran must not read as an audit that passed
# ----------------------------------------------------------------------
#
# A job that dies before `Audit each changed skill` completes (a rate-limited
# `GITHUB_TOKEN`, a broken install) leaves every count output unset. The gate
# used to read them as `${BLOCK_COUNT:-0}` and print "Skill audit passed." for a
# run that audited nothing — the silent wrong answer in #4107. These tests run
# the step's real shell body so the behaviour, not its spelling, is asserted.

import os  # noqa: E402
import subprocess  # noqa: E402

#: Windows ships `C:\Windows\System32\bash.exe` as a WSL launcher that exists
#: even when no distribution is installed, so a PATH lookup says yes and every
#: invocation fails. Probe by running a trivial script instead.
_BASH_PROBE = ["bash", "-c", "exit 0"]

#: Every count `Audit each changed skill` publishes; the verdict refuses to read
#: a partial set, so all of them are required for a meaningful check.
REQUIRED_COUNTS = (
    "BLOCK_COUNT",
    "REVIEW_COUNT",
    "HUB_REVIEW_COUNT",
    "INVALID_COUNT",
    "ALLOW_COUNT",
)

INFRA_MARKER = "Skill audit (infrastructure)"
POLICY_MARKER = "::error title=Skill audit::"


def _verdict(
    enforce_step: dict,
    *,
    audit_outcome: str,
    counts: dict[str, str],
    seconds: int = 30,
) -> subprocess.CompletedProcess:
    """Run the verdict step with the `${{ steps.* }}` expressions resolved.

    `AUDIT_OUTCOME` carries the `steps.audit.outcome` value the runner would
    substitute; `counts` are the step outputs it would also substitute.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB")}
    env.update(
        {
            "AUDIT_OUTCOME": audit_outcome,
            "SIGNED_OFF": "false",
            "GITHUB_STEP_SUMMARY": os.devnull,
        }
    )
    for name, value in counts.items():
        env[name] = value
    return subprocess.run(
        ["bash", "-c", enforce_step["run"]],
        env=env,
        capture_output=True,
        text=True,
        timeout=seconds,
        check=False,
    )


#: Fixed so the shell can run the body offline; the value never reaches a check.
_RUNNER_SUBSTITUTIONS = {
    "github.event.pull_request.labels.*.name": "['none']",
}


@pytest.fixture(scope="module")
def runnable_enforce(enforce_step: dict) -> dict:
    try:
        probe = subprocess.run(
            _BASH_PROBE, capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"the verdict step is a bash script; bash cannot run: {exc}")
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout).strip()
        pytest.skip(f"the verdict step is a bash script; bash cannot run: {detail}")
    run = enforce_step["run"]
    for expression, value in _RUNNER_SUBSTITUTIONS.items():
        run = run.replace("${{ " + expression + " }}", value)
    assert "${{" not in run, "every expression in the verdict step must be resolved"
    return {**enforce_step, "run": run}


def test_a_clean_audit_passes(runnable_enforce):
    """Control: the happy path still passes, so the failures below mean something."""
    result = _verdict(
        runnable_enforce,
        audit_outcome="success",
        counts=dict.fromkeys(REQUIRED_COUNTS, "0"),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Skill audit passed." in result.stdout
    assert INFRA_MARKER not in result.stdout


def test_a_block_from_an_audit_that_ran_is_a_policy_failure(
    enforce_step, runnable_enforce
):
    """A real violation keeps its own message: it is not an infrastructure fault."""
    result = _verdict(
        runnable_enforce,
        audit_outcome="success",
        counts={**dict.fromkeys(REQUIRED_COUNTS, "0"), "BLOCK_COUNT": "1"},
    )
    assert result.returncode != 0
    assert POLICY_MARKER in result.stdout
    assert "BLOCKED" in result.stdout
    assert INFRA_MARKER not in result.stdout
    assert "Skill audit passed." not in result.stdout


def test_a_skipped_audit_fails_as_infrastructure(runnable_enforce):
    """The #4107 case: the job died upstream, so the gate never ran."""
    result = _verdict(
        runnable_enforce,
        audit_outcome="skipped",
        counts=dict.fromkeys(REQUIRED_COUNTS, "0"),
    )
    assert result.returncode != 0, "an audit that never ran must not pass"
    assert INFRA_MARKER in result.stdout
    assert "Skill audit passed." not in result.stdout


def test_a_failed_audit_fails_as_infrastructure(runnable_enforce):
    result = _verdict(
        runnable_enforce,
        audit_outcome="failure",
        counts=dict.fromkeys(REQUIRED_COUNTS, "0"),
    )
    assert result.returncode != 0
    assert INFRA_MARKER in result.stdout
    assert "Skill audit passed." not in result.stdout


@pytest.mark.parametrize("missing", REQUIRED_COUNTS)
def test_a_missing_count_is_infrastructure_not_zero(runnable_enforce, missing):
    """Every required count is checked, not just the one that happens to be read first."""
    counts = {name: "0" for name in REQUIRED_COUNTS if name != missing}
    result = _verdict(runnable_enforce, audit_outcome="success", counts=counts)
    assert result.returncode != 0, f"an unset {missing} must not read as 0"
    assert INFRA_MARKER in result.stdout
    assert "Skill audit passed." not in result.stdout


@pytest.mark.parametrize("bad", ["", " ", "1.5", "one", "-1", "1 2"])
def test_a_malformed_count_is_infrastructure_not_zero(runnable_enforce, bad):
    """Each is a value `[ -gt 0 ]` either dies on or silently misreads."""
    counts = {**dict.fromkeys(REQUIRED_COUNTS, "0"), "BLOCK_COUNT": bad}
    result = _verdict(runnable_enforce, audit_outcome="success", counts=counts)
    assert result.returncode != 0, f"BLOCK_COUNT={bad!r} must not read as 0"
    assert INFRA_MARKER in result.stdout
    assert "Skill audit passed." not in result.stdout


def test_the_verdict_runs_after_an_upstream_failure(enforce_step):
    """A status function is required, or the infrastructure verdict never fires.

    Actions wraps a step `if:` that names no status function in an implicit
    `success()`. A failed install or audit would then skip this step entirely,
    which is the #4107 hole: the gate disappears instead of reporting that it
    never ran. This asserts the condition, not just the shell body, because the
    runner decides whether that body runs at all.
    """
    assert "always()" in str(enforce_step.get("if", "")), (
        "Enforce verdict must run after an upstream failure to report it; "
        "drop always() and the infrastructure branch becomes unreachable"
    )


def test_the_gate_does_not_read_the_downstream_skills_output(enforce_step):
    """Keeping the downstream `skills` signal out of the verdict.

    A future change may empty `skills` so the SARIF and Claude jobs skip an
    incomplete run. If the verdict also consulted it, one place would decide
    both, and a downstream skip could turn the required gate green. The verdict
    keys on `AUDIT_OUTCOME` instead, so the two cannot be conflated.
    """
    assert "SKILLS" not in enforce_step.get("env", {})
    assert "steps.detect" not in enforce_step["run"]
    assert "AUDIT_OUTCOME" in enforce_step["run"]


# ----------------------------------------------------------------------
# Best-effort PR summary
# ----------------------------------------------------------------------


def _summary_comment_step(gate: dict) -> dict:
    steps = [
        step
        for step in gate["steps"]
        if step.get("name") == "Post summary-only PR comment"
    ]
    assert steps, "the workflow must retain its same-repo summary-comment step"
    return steps[0]


def test_rate_limited_summary_comment_cannot_fail_the_audit_gate(gate):
    """The deterministic verdict, not a GitHub API call, decides this check."""
    assert _summary_comment_step(gate).get("continue-on-error") is True


def test_summary_comment_lookup_is_paginated_to_update_existing_marker(gate):
    """A marker beyond GitHub's default page must not create a duplicate."""
    script = _summary_comment_step(gate)["with"]["script"]
    assert "github.paginate" in script
    assert "github.rest.issues.listComments" in script
    assert "per_page: 100" in script


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
    """Contributors need somewhere to PR into, alongside the hub/skills/ pack."""
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
