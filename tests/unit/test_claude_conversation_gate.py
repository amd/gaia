# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Claude follows up in threads it is part of, without an @claude mention (#1408).

Before, issue-handler fired only on a new issue or a comment containing
``@claude``. On #1394 a reporter answered Claude's own repro question and sat
unanswered for 90 minutes until a maintainer typed the mention. Now a cheap
``conversation-gate`` job lets a mention-less comment through when the thread
is labelled ``claude`` or is an issue the bot already replied on, and the
prompt decides whether replying adds anything.

What these tests pin:

- the loop-breaker: bot comments never pass the gate, and edits don't either;
- the gate only reads (it holds no write scope and no Claude secret);
- issue-handler still runs for new issues and mentions when the gate is skipped;
- the gate's decision, by running its real ``run:`` script against a stubbed gh.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "claude.yml"


@pytest.fixture(scope="module")
def jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


@pytest.fixture(scope="module")
def gate(jobs) -> dict:
    assert "conversation-gate" in jobs
    return jobs["conversation-gate"]


@pytest.fixture(scope="module")
def handler(jobs) -> dict:
    return jobs["issue-handler"]


def _flat(expr: str) -> str:
    return " ".join(expr.split())


# --- the gate's trigger condition ------------------------------------------


@pytest.mark.parametrize(
    "clause",
    [
        "github.repository == 'amd/gaia'",
        "github.event_name == 'issue_comment'",
        "github.event.action == 'created'",
        "github.event.comment.user.type != 'Bot'",
        "!contains(github.event.comment.body, '@claude')",
    ],
)
def test_gate_condition_keeps_each_guard(gate, clause):
    cond = _flat(gate["if"])
    assert clause in cond, f"conversation-gate lost {clause!r}"
    assert " || " not in cond, "every guard must be AND-ed, or one can be bypassed"


def test_gate_only_reads(gate):
    assert gate["permissions"] == {"issues": "read", "pull-requests": "read"}
    text = yaml.safe_dump(gate)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in text and "ANTHROPIC_API_KEY" not in text
    assert "secrets: inherit" not in text
    assert isinstance(gate.get("timeout-minutes"), int)


def test_gate_never_interpolates_comment_text_into_shell(gate):
    for step in gate["steps"]:
        assert "${{" not in step.get("run", "")
        assert "comment.body" not in yaml.safe_dump(step.get("env", {}))


# --- issue-handler wiring --------------------------------------------------


def test_handler_needs_the_gate_but_survives_its_skip(handler):
    assert handler["needs"] == "conversation-gate"
    cond = _flat(handler["if"])
    assert cond.startswith(
        "!cancelled() &&"
    ), "without !cancelled() a skipped gate (new issue, @claude) skips the handler"
    assert "needs.conversation-gate.outputs.engaged == 'true'" in cond


def test_handler_still_answers_new_issues_and_mentions(handler):
    cond = _flat(handler["if"])
    assert "(github.event_name == 'issues' && github.event.action == 'opened')" in cond
    assert "contains(github.event.comment.body, '@claude')" in cond
    assert "github.repository == 'amd/gaia'" in cond


def test_handler_scope_is_unchanged(handler):
    """Wider triggers must not come with wider rights: fork PRs reach this job."""
    assert handler["permissions"] == {
        "contents": "read",
        "issues": "write",
        "pull-requests": "write",
    }


def test_prompt_carries_the_self_gate(handler):
    prompt = handler["with"]["prompt"]
    assert (
        "DIRECT MENTION: ${{ github.event_name == 'issues' || "
        "contains(github.event.comment.body, '@claude') }}" in prompt
    )
    assert "Respond or stay silent" in prompt
    assert "question YOU asked" in prompt, "the #1394 case must clear the bar"
    assert "post NOTHING" in prompt


# --- behaviour: run the gate's real script ----------------------------------

_GH_STUB = """#!/bin/sh
echo "$*" >> "$STUB_LOG"
if [ -n "$STUB_FAIL" ]; then echo "gh: HTTP 502" >&2; exit 1; fi
for n in $STUB_PAGES; do echo "$n"; done
"""


def _run_gate(gate, tmp_path, *, labelled="false", is_pr="false", pages="0", fail=""):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "gh"
    stub.write_text(_GH_STUB, encoding="utf-8", newline="\n")
    stub.chmod(0o755)
    out = tmp_path / "out"
    log = tmp_path / "gh.log"
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(out),
        "STUB_LOG": str(log),
        "STUB_PAGES": pages,
        "STUB_FAIL": fail,
        "REPO": "amd/gaia",
        "NUMBER": "1394",
        "LABELLED": labelled,
        "IS_PR": is_pr,
    }
    (step,) = gate["steps"]
    res = subprocess.run(
        [bash, "-c", step["run"]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    written = out.read_text(encoding="utf-8") if out.exists() else ""
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return res, written, calls


def test_labelled_thread_is_engaged_without_an_api_call(gate, tmp_path):
    res, out, calls = _run_gate(gate, tmp_path, labelled="true", is_pr="true")
    assert res.returncode == 0, res.stderr
    assert out.strip() == "engaged=true"
    assert calls == []


def test_unlabelled_pr_is_not_engaged_even_with_bot_comments(gate, tmp_path):
    res, out, calls = _run_gate(gate, tmp_path, is_pr="true", pages="3")
    assert res.returncode == 0, res.stderr
    assert out.strip() == "engaged=false"
    assert calls == []


def test_issue_claude_replied_on_is_engaged(gate, tmp_path):
    res, out, calls = _run_gate(gate, tmp_path, pages="1")
    assert res.returncode == 0, res.stderr
    assert out.strip() == "engaged=true"
    (call,) = calls
    assert call.startswith("api --paginate repos/amd/gaia/issues/1394/comments")
    assert 'select(.user.login == "github-actions[bot]")' in call


def test_replies_on_a_later_page_count(gate, tmp_path):
    res, out, _ = _run_gate(gate, tmp_path, pages="0 0 2")
    assert out.strip() == "engaged=true"


def test_issue_without_a_bot_reply_is_not_engaged(gate, tmp_path):
    res, out, _ = _run_gate(gate, tmp_path, pages="0 0")
    assert res.returncode == 0, res.stderr
    assert out.strip() == "engaged=false"


def test_api_failure_fails_the_gate_instead_of_guessing(gate, tmp_path):
    res, out, _ = _run_gate(gate, tmp_path, fail="1")
    assert res.returncode != 0
    assert "engaged=" not in out
