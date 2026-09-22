# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Guards the PR-review posting and outage-reporting contract in .github/workflows/.

Two failure modes, one file. Claude WRITES its review to a file and a workflow step
POSTS it: when the model was asked to run `gh pr comment` itself, a skipped final tool
call published nothing while the job stayed green — 50 PRs merged unreviewed before
anyone noticed. And when the review bot never reaches a model at all (spend cap,
rejected credential, install crash), the lane must say THAT rather than rendering like
a review that ran and failed — days went into telling those apart by hand (#4080).
These tests fail if either arrangement is reintroduced.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
REVIEW_JOBS = ("pr-review", "pr-rereview")

# Verbatim from the failing `pr-review / run` lanes of PRs #4119 and #4120: the CLI
# returned in ~0.4s having billed nothing, so no model ever saw the diff.
OUTAGE_RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": True,
    "duration_ms": 394,
    "num_turns": 1,
    "total_cost_usd": 0,
    "permission_denials_count": 0,
    "modelUsage": {},
}

# `gh pr comment N --body-file X` / `gh issue comment ...` — the executable posting
# form. A bare "do NOT run `gh pr comment`" prohibition has no --body-file and is fine.
POSTING_CMD = re.compile(r"gh\s+(?:pr|issue)\s+comment\b[^\n]*--body-file")


def _load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def claude():
    return _load("claude.yml")


@pytest.fixture(scope="module")
def runner():
    return _load("claude-run.yml")


@pytest.mark.parametrize("job,require", [("pr-review", True), ("pr-rereview", False)])
def test_review_jobs_delegate_posting_to_the_workflow(claude, job, require):
    """Both review jobs hand a file to claude-run.yml instead of posting inline."""
    with_ = claude["jobs"][job]["with"]
    assert with_.get("comment_file"), f"{job} must set comment_file"
    # pr-review: a review is mandatory, so no file is a failure. pr-rereview:
    # REVIEW.md makes silence correct, so no file is a legitimate no-op.
    assert with_.get("require_comment", False) is require


@pytest.mark.parametrize("job", REVIEW_JOBS)
def test_review_prompts_never_tell_the_model_to_post(claude, job):
    """The model must not be handed a posting command — that path fails silently."""
    prompt = claude["jobs"][job]["with"]["prompt"]
    found = POSTING_CMD.search(prompt)
    assert found is None, (
        f"{job}'s prompt tells the model to post: {found.group(0)!r}. "
        "Posting belongs to claude-run.yml's 'Post Claude's comment' step; "
        "a model-issued post is invisible when it doesn't happen."
    )


def test_runner_posts_and_is_gated_on_comment_file(runner):
    steps = runner["jobs"]["run"]["steps"]
    post = next((s for s in steps if s.get("name") == "Post Claude's comment"), None)
    assert post is not None, "claude-run.yml lost its posting step"
    assert "inputs.comment_file != ''" in post["if"], (
        "posting must be gated on comment_file so callers that still post inline "
        "(issue-handler, pr-comment) don't double-post"
    )
    assert POSTING_CMD.search(post["run"]), "posting step no longer posts anything"


def test_runner_declares_the_posting_inputs(runner):
    inputs = runner[True]["workflow_call"]["inputs"]  # PyYAML reads `on:` as True
    assert inputs["comment_file"]["default"] == ""
    assert inputs["require_comment"]["default"] is False


# --- "the bot never ran" must not render like "the bot reviewed and failed" ---


def _working_bash():
    """Resolve a bash that can actually run a script.

    CreateProcess searches System32 before PATH, and on Windows that is the WSL
    shim, which errors out when no distro is installed. Probe instead of assuming.
    """
    found = shutil.which("bash")
    if not found or not shutil.which("jq"):
        return None
    try:
        probe = subprocess.run(
            [found, "-c", "echo ok"], capture_output=True, text=True, timeout=30
        )
    except OSError:
        return None
    return found if probe.stdout.strip() == "ok" else None


BASH = _working_bash()
needs_shell = pytest.mark.skipif(
    BASH is None, reason="needs a working bash + jq (both present on the GitHub runner)"
)


def _step(runner, predicate):
    return next(
        (s for s in runner["jobs"]["run"]["steps"] if predicate(s.get("name") or "")),
        None,
    )


@pytest.fixture(scope="module")
def classify(runner):
    step = next(s for s in runner["jobs"]["run"]["steps"] if s.get("id") == "harness")
    return step


def test_the_run_is_classified_from_the_artifact_not_the_step_outcome(classify):
    """continue-on-error masks the outcome, so only execution_file can be trusted."""
    exec_file = classify["env"]["EXEC_FILE"]
    assert "steps.a1.outputs.execution_file" in exec_file
    assert (
        "steps.a2.outputs.execution_file" in exec_file
    ), "must cover the retry attempt"


def test_the_outage_verdict_keys_on_usage_not_on_wording(classify):
    """The rejection text moves (reset date, U+00B7, often absent); the numbers don't."""
    body = classify["run"]
    assert "modelUsage" in body and "total_cost_usd" in body and "is_error" in body
    # A pattern match on the refusal text must never be what decides "outage".
    verdict = body.split("status=$(")[1].split("')")[0]
    for wording in ("weekly", "limit", "quota", "spend"):
        assert wording not in verdict.lower(), (
            f"the status expression matches on {wording!r} — key on the measurable "
            "facts, not the rejection wording (#4080)"
        )


@pytest.mark.parametrize(
    "status,needle",
    [
        ("not_started", "never reached a model"),
        ("no_output", "crashed before starting"),
    ],
)
def test_each_outage_gets_its_own_named_failing_step(runner, status, needle):
    """The step NAME is what a reader sees in the checks list — the cause lives there."""
    step = _step(runner, lambda n: needle in n)
    assert step is not None, f"no step names the {status} outage"
    assert f"status == '{status}'" in step["if"]
    assert step.get("continue-on-error") is None, "the red must stay red"
    assert "exit 1" in step["run"], "an outage on a require_comment lane must fail"
    assert "NEVER RAN" in step["run"], "say the review never ran, not that it failed"


def test_every_verdict_has_exactly_one_named_step(runner):
    """Four verdicts, four names — a shared step would bury the cause in a log line."""
    named = {
        status: [
            s
            for s in runner["jobs"]["run"]["steps"]
            if f"status == '{status}'" in (s.get("if") or "")
        ]
        for status in ("ok", "errored", "not_started", "no_output")
    }
    assert {k: len(v) for k, v in named.items()} == dict.fromkeys(named, 1), named
    # An error that DID reach the model is a real finding, not an outage — saying
    # "never ran" there would swap the two disguises rather than remove them.
    errored = named["errored"][0]["run"]
    assert "NEVER RAN" not in errored
    assert "REACHED the model" in errored


def test_the_install_crash_is_only_blamed_when_there_is_no_log(runner):
    """#4119's disguise: a spend cap reported as 'the action crashed (ENOENT flake)'."""
    for step in runner["jobs"]["run"]["steps"]:
        if "ENOENT" not in (step.get("run") or ""):
            continue
        assert "no_output" in step["if"], (
            f"step {step.get('name')!r} blames an install crash without having "
            "established that no execution log exists"
        )


def test_posting_defers_to_the_named_outage_step(runner):
    """One cause, one failure — not the generic 'no review' error on top of it."""
    post = _step(runner, lambda n: n == "Post Claude's comment")
    assert "not_started|no_output" in post["run"]
    assert post["env"]["HARNESS_STATUS"] == "${{ steps.harness.outputs.status }}"


@needs_shell
@pytest.mark.parametrize(
    "name,records,expected",
    [
        ("real outage", [OUTAGE_RESULT], "not_started"),
        (
            "review that ran",
            [
                {
                    "type": "result",
                    "is_error": False,
                    "num_turns": 12,
                    "total_cost_usd": 0.42,
                    "modelUsage": {"claude-opus-5": {"in": 1}},
                }
            ],
            "ok",
        ),
        (
            # A Claude subscription bills nothing per request, so zero cost alone must
            # never read as an outage — only zero cost WITH an empty usage map does.
            "subscription run, zero cost",
            [
                {
                    "type": "result",
                    "is_error": False,
                    "num_turns": 5,
                    "total_cost_usd": 0,
                    "modelUsage": {"claude-opus-5": {"in": 1}},
                }
            ],
            "ok",
        ),
        (
            # The case zero-cost-alone would get wrong: a subscription run that DID
            # reach the model and then failed. Tokens were spent; only dollars weren't.
            "subscription run that errored",
            [
                {
                    "type": "result",
                    "is_error": True,
                    "num_turns": 7,
                    "total_cost_usd": 0,
                    "modelUsage": {"claude-opus-5": {"in": 9000}},
                    "result": "Error: max turns exceeded",
                }
            ],
            "errored",
        ),
        (
            "errored after reaching the model",
            [
                {
                    "type": "result",
                    "is_error": True,
                    "num_turns": 9,
                    "total_cost_usd": 0.31,
                    "modelUsage": {"claude-opus-5": {"in": 1}},
                    "result": "Error: tool use failed",
                }
            ],
            "errored",
        ),
        (
            # Billed something with no usage map: whatever happened, it wasn't a
            # refusal before any work. "No billable evidence of ANY kind" is the rule.
            "billed but no usage map",
            [
                {
                    "type": "result",
                    "is_error": True,
                    "num_turns": 3,
                    "total_cost_usd": 0.05,
                    "modelUsage": {},
                }
            ],
            "errored",
        ),
        (
            # A failed tool call also carries is_error; the CLI's result record wins.
            "tool error then outage",
            [
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_result", "is_error": True}]},
                },
                OUTAGE_RESULT,
            ],
            "not_started",
        ),
        (
            # No result record at all — a tool error must not be read as the verdict.
            "tool error and nothing else",
            [
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_result", "is_error": True}]},
                }
            ],
            "no_output",
        ),
    ],
)
def test_the_classifier_reads_a_real_execution_log(
    classify, tmp_path, name, records, expected
):
    log = tmp_path / "execution.json"
    log.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    assert _run_classify(classify, tmp_path, log)["status"] == expected, name


@needs_shell
@pytest.mark.parametrize("contents", [None, "", "not json at all"])
def test_an_unusable_log_is_a_crash_not_a_clean_review(classify, tmp_path, contents):
    log = tmp_path / "execution.json"
    if contents is not None:
        log.write_text(contents, encoding="utf-8")
    assert _run_classify(classify, tmp_path, log)["status"] == "no_output"


def _run_classify(classify, tmp_path, log):
    """Execute the step's real `run:` body and return its $GITHUB_OUTPUT."""
    script = tmp_path / "classify.sh"
    script.write_text(classify["run"], encoding="utf-8", newline="\n")
    out = tmp_path / "gh_output"
    out.touch()
    proc = subprocess.run(
        [BASH, script.as_posix()],
        env={
            "PATH": os.environ["PATH"],
            "EXEC_FILE": log.as_posix() if log.exists() else "",
            "GITHUB_OUTPUT": out.as_posix(),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return dict(
        line.split("=", 1)
        for line in out.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
