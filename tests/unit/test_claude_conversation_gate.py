# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Claude follows up in threads it is part of, without an @claude mention (#1408).

Before, issue-handler fired only on a new issue or a comment containing
``@claude``. On #1394 a reporter answered Claude's own repro question and sat
unanswered for 90 minutes until a maintainer typed the mention. Now a cheap
``conversation-gate`` job lets a maintainer's mention-less comment through when
the thread is labelled ``claude`` or the comment directly follows a bot reply
on an issue, and the prompt decides whether replying adds anything.

What these tests pin:

- cost: only OWNER / MEMBER / COLLABORATOR comments pass, so an outsider
  (including whoever opened the thread) can't start Opus runs without @claude;
- a per-thread hourly cap on bot replies, and a per-thread concurrency group
  that collapses a burst of follow-ups;
- the loop-breaker: bot comments never pass the gate, and edits don't either;
- the gate only reads (it holds no write scope and no Claude secret);
- issue-handler still runs for new issues and mentions when the gate is skipped;
- the gate's decision, by running its real ``run:`` script against a stubbed gh.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "claude.yml"

MAINTAINER_ROLES = ("OWNER", "MEMBER", "COLLABORATOR")
OUTSIDER_ROLES = (
    "CONTRIBUTOR",
    "FIRST_TIME_CONTRIBUTOR",
    "FIRST_TIMER",
    "MANNEQUIN",
    "NONE",
)


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


@pytest.fixture(scope="module")
def cap(gate) -> int:
    return int(gate["steps"][0]["env"]["MAX_RECENT_REPLIES"])


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
        'contains(fromJSON(\'["OWNER","MEMBER","COLLABORATOR"]\'), '
        "github.event.comment.author_association)",
        "!contains(github.event.comment.body, '@claude')",
    ],
)
def test_gate_condition_keeps_each_guard(gate, clause):
    cond = _flat(gate["if"])
    assert clause in cond, f"conversation-gate lost {clause!r}"
    assert " || " not in cond, "every guard must be AND-ed, or one can be bypassed"


def test_gate_allowlist_has_no_outsider_role(gate):
    cond = _flat(gate["if"])
    for role in OUTSIDER_ROLES:
        assert f'"{role}"' not in cond, f"{role} must not pass the gate"


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


def test_followups_share_a_per_thread_concurrency_group(handler):
    conc = handler["concurrency"]
    assert conc["cancel-in-progress"] is False, "a running reply must not be killed"
    group = _flat(conc["group"])
    assert (
        "needs.conversation-gate.outputs.engaged == 'true' && "
        "format('claude-followup-{0}', github.event.issue.number)" in group
    )
    # Mentions and new issues get a per-run group, so none of them is cancelled.
    assert "github.run_id" in group


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

BOT = "github-actions[bot]"
TRIGGER_ID = 999
# The stub plays gh's --jq output: one "<id> <login> <epoch>" line per comment.
_GH_STUB = """#!/bin/sh
echo "$*" >> "$STUB_LOG"
if [ -n "$STUB_FAIL" ]; then echo "gh: HTTP 502" >&2; exit 1; fi
cat "$STUB_COMMENTS"
"""


def _thread(*earlier, trigger=True, after=()):
    """(login, minutes_ago) pairs, oldest first; the trigger comment is appended."""
    now = int(time.time())
    rows = [
        (100 + i, login, now - mins * 60) for i, (login, mins) in enumerate(earlier)
    ]
    if trigger:
        rows.append((TRIGGER_ID, "amd-pworfolk", now))
    rows += [(2000 + i, login, now) for i, login in enumerate(after)]
    return "".join(f"{i} {login} {ts}\n" for i, login, ts in rows)


def _run_gate(
    gate,
    tmp_path,
    *,
    association="MEMBER",
    labelled="false",
    is_pr="false",
    comments=None,
    fail="",
):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "gh"
    stub.write_text(_GH_STUB, encoding="utf-8", newline="\n")
    stub.chmod(0o755)
    feed = tmp_path / "comments.txt"
    feed.write_text(
        _thread((BOT, 10)) if comments is None else comments,
        encoding="utf-8",
        newline="\n",
    )
    out = tmp_path / "out"
    log = tmp_path / "gh.log"
    (step,) = gate["steps"]
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(out),
        "STUB_LOG": str(log),
        "STUB_COMMENTS": str(feed),
        "STUB_FAIL": fail,
        "REPO": "amd/gaia",
        "NUMBER": "1394",
        "COMMENT_ID": str(TRIGGER_ID),
        "ASSOCIATION": association,
        "LABELLED": labelled,
        "IS_PR": is_pr,
        "MAX_RECENT_REPLIES": str(step["env"]["MAX_RECENT_REPLIES"]),
    }
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


def test_gate_reads_the_comment_it_was_triggered_by(gate):
    env = gate["steps"][0]["env"]
    assert env["COMMENT_ID"] == "${{ github.event.comment.id }}"
    assert env["ASSOCIATION"] == "${{ github.event.comment.author_association }}"


@pytest.mark.parametrize("association", MAINTAINER_ROLES)
def test_maintainer_answering_claude_is_engaged(gate, tmp_path, association):
    res, out, _ = _run_gate(gate, tmp_path, association=association)
    assert res.returncode == 0, res.stderr
    assert out.strip() == "engaged=true"


@pytest.mark.parametrize(
    "association",
    [*OUTSIDER_ROLES, "", "member", "MEMBER ", "MEMBER|OWNER", "*"],
)
def test_outsider_is_not_engaged_and_costs_no_api_call(gate, tmp_path, association):
    res, out, calls = _run_gate(
        gate, tmp_path, association=association, labelled="true"
    )
    assert res.returncode == 0, res.stderr
    assert out.strip() == "engaged=false"
    assert calls == []


def test_outside_issue_author_answering_claude_is_not_engaged(gate, tmp_path):
    """A reporter's comment carries NONE even on their own issue: @claude only."""
    res, out, calls = _run_gate(gate, tmp_path, association="NONE")
    assert out.strip() == "engaged=false"
    assert calls == []
    assert "@claude" in res.stdout


def test_labelled_thread_is_engaged_for_a_maintainer(gate, tmp_path):
    res, out, calls = _run_gate(
        gate, tmp_path, labelled="true", is_pr="true", comments=_thread()
    )
    assert res.returncode == 0, res.stderr
    assert out.strip() == "engaged=true", "a labelled thread needs no prior bot reply"
    assert sum(c.startswith("api ") for c in calls) == 1
    call = "\n".join(calls)
    assert call.startswith("api --paginate repos/amd/gaia/issues/1394/comments")


def test_unlabelled_pr_is_not_engaged_even_right_after_a_bot_comment(gate, tmp_path):
    res, out, calls = _run_gate(gate, tmp_path, is_pr="true")
    assert res.returncode == 0, res.stderr
    assert out.strip() == "engaged=false"
    assert calls == []


def test_issue_comment_right_after_claude_is_engaged(gate, tmp_path):
    res, out, calls = _run_gate(
        gate, tmp_path, comments=_thread(("alice", 90), (BOT, 60))
    )
    assert res.returncode == 0, res.stderr
    assert out.strip() == "engaged=true"
    assert sum(c.startswith("api ") for c in calls) == 1


@pytest.mark.parametrize(
    "earlier",
    [
        (),  # Claude never replied
        ((BOT, 600), ("alice", 300)),  # someone else spoke after Claude
        ((BOT, 600), ("amd-pworfolk", 5)),  # a second maintainer comment in a row
        (("github-actions", 5),),  # a user named like the bot is not the bot
        (("claude[bot]", 5),),  # another bot is not this one
    ],
)
def test_issue_comment_not_answering_claude_is_not_engaged(gate, tmp_path, earlier):
    res, out, _ = _run_gate(gate, tmp_path, comments=_thread(*earlier))
    assert res.returncode == 0, res.stderr
    assert out.strip() == "engaged=false"
    assert "does not directly follow" in res.stdout


def test_trigger_missing_from_the_listing_fails_closed(gate, tmp_path):
    _, out, _ = _run_gate(gate, tmp_path, comments=_thread((BOT, 5), trigger=False))
    assert out.strip() == "engaged=false"


def test_later_comments_do_not_change_what_the_trigger_follows(gate, tmp_path):
    feed = _thread((BOT, 5), after=("alice", BOT))
    _, out, _ = _run_gate(gate, tmp_path, comments=feed)
    assert out.strip() == "engaged=true"


def test_api_failure_fails_the_gate_loudly(gate, tmp_path):
    res, out, _ = _run_gate(gate, tmp_path, fail="1")
    assert res.returncode != 0
    assert "engaged=" not in out
    assert "::error::" in res.stdout and "#1394" in res.stdout


# --- burst / loop guard ------------------------------------------------------


def test_cap_is_small(cap):
    assert 1 <= cap <= 5


def test_just_below_the_hourly_cap_is_engaged(gate, tmp_path, cap):
    old = [(BOT, 600 + i) for i in range(9)]
    recent = [(BOT, 30 - i) for i in range(cap - 1)]
    _, out, _ = _run_gate(gate, tmp_path, comments=_thread(*old, *recent))
    assert out.strip() == "engaged=true"


@pytest.mark.parametrize("labelled", ["true", "false"])
def test_hourly_cap_stops_a_loop(gate, tmp_path, cap, labelled):
    recent = [(BOT, 50 - i) for i in range(cap)]
    res, out, _ = _run_gate(
        gate, tmp_path, labelled=labelled, comments=_thread(*recent)
    )
    assert res.returncode == 0, res.stderr
    assert out.strip() == "engaged=false"
    assert "last hour" in res.stdout


def test_replies_older_than_an_hour_do_not_count(gate, tmp_path, cap):
    old = [(BOT, 61 + i) for i in range(cap * 3)]
    _, out, _ = _run_gate(gate, tmp_path, comments=_thread(*old))
    assert out.strip() == "engaged=true"


def test_jq_filter_emits_id_login_epoch(gate):
    """Run the gate's real jq filter over a comments page."""
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq not available")
    m = re.search(r"--jq '(.*?)'", gate["steps"][0]["run"], re.S)
    assert m, "gate lost its --jq filter"
    page = [
        {"id": 7, "user": {"login": BOT}, "created_at": "2026-06-04T16:56:10Z"},
        {
            "id": 8,
            "user": {"login": "amd-pworfolk"},
            "created_at": "2026-06-04T16:53:38Z",
        },
    ]
    res = subprocess.run(
        [jq, "-r", m.group(1)],
        input=json.dumps(page),
        capture_output=True,
        text=True,
        check=True,
    )
    expected = [
        f"7 {BOT} {int(datetime(2026, 6, 4, 16, 56, 10, tzinfo=timezone.utc).timestamp())}",
        f"8 amd-pworfolk {int(datetime(2026, 6, 4, 16, 53, 38, tzinfo=timezone.utc).timestamp())}",
    ]
    assert res.stdout.splitlines() == expected
