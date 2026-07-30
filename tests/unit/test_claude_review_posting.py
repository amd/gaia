# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Guards the PR-review posting contract in .github/workflows/.

Claude WRITES its review to a file; a workflow step POSTS it. When the model was
asked to run `gh pr comment` itself, a skipped final tool call published nothing
while the job stayed green — 50 PRs merged unreviewed before anyone noticed.
These tests fail if that arrangement is reintroduced.
"""

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
REVIEW_JOBS = ("pr-review", "pr-rereview")

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


@pytest.mark.parametrize(
    "job,require", [("pr-review", True), ("pr-rereview", False)]
)
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
