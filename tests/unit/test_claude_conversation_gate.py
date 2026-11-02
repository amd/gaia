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
- "follows Claude" means a reply carrying the marker the posting step appends,
  not any github-actions[bot] comment;
- a per-thread hourly cap on the gate's own passes, and separate per-thread
  queues for mentions and follow-ups, so a follow-up never displaces a mention
  and a skipped run never enters either queue;
- the loop-breaker: bot comments never pass the gate, and edits start no run on
  either the gate or the @claude path;
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
RUNNER = REPO_ROOT / ".github" / "workflows" / "claude-run.yml"

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
    return int(gate["steps"][0]["env"]["MAX_PASSES_PER_HOUR"])


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
    assert "github.repository == 'amd/gaia'" in cond


def test_editing_a_comment_never_starts_a_run(handler, gate):
    """Each OR branch must be a creation event, or the gate (which needs `created`)."""
    cond = _flat(handler["if"])
    assert (
        "(github.event_name == 'issue_comment' && "
        "github.event.action == 'created' && "
        "contains(github.event.comment.body, '@claude'))" in cond
    ), "an @claude comment edited N times would start N+1 runs"
    assert "github.event.action == 'created'" in _flat(gate["if"])
    branches = cond.split("!cancelled() && github.repository == 'amd/gaia' && ", 1)[1]
    assert branches.count(" || ") == 2, "a new trigger branch needs its own edit guard"


def _eval(expr: str, ctx: dict):
    """Evaluate the small GitHub-expression subset these jobs use.

    `&&` / `||` return an operand like Python's `and` / `or`, so the format()
    fallbacks behave as on GitHub. Unknown paths resolve to None.
    """
    expr = _flat(expr)
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2].strip()

    def lookup(path):
        node = ctx
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    py = expr.replace("!cancelled()", "True")
    py = re.sub(r"\b((?:github|needs)\.[\w.-]+)", lambda m: f"_get({m.group(1)!r})", py)
    py = py.replace("&&", " and ").replace("||", " or ")
    assert "!" not in py.replace("!=", ""), f"unsupported operator in {expr!r}"
    return eval(  # noqa: S307 - the input is this repo's own workflow file
        py,
        {"__builtins__": {}},
        {
            "_get": lookup,
            "True": True,
            "contains": lambda hay, needle: needle in (hay or ""),
            "format": lambda fmt, *a: fmt.format(*a),
        },
    )


def _event(name, action, body=None, engaged=""):
    event = {"action": action, "issue": {"number": 42}}
    if body is not None:
        event["comment"] = {"body": body}
    return {
        "github": {
            "repository": "amd/gaia",
            "event_name": name,
            "run_id": "7001",
            "event": event,
        },
        "needs": {"conversation-gate": {"outputs": {"engaged": engaged}}},
    }


QUEUE_CASES = {
    "new-issue": (_event("issues", "opened"), True, "claude-mention-42"),
    "mention": (
        _event("issue_comment", "created", "@claude why?"),
        True,
        "claude-mention-42",
    ),
    "follow-up": (
        _event("issue_comment", "created", "thanks", engaged="true"),
        True,
        "claude-followup-42",
    ),
    "declined": (
        _event("issue_comment", "created", "thanks", engaged="false"),
        False,
        None,
    ),
    "edited-mention": (
        _event("issue_comment", "edited", "@claude why?"),
        False,
        None,
    ),
    "edited-follow-up": (_event("issue_comment", "edited", "thanks"), False, None),
    "issue-edited": (_event("issues", "edited"), False, None),
    "pr-event": (_event("pull_request_target", "opened"), False, None),
}


@pytest.mark.parametrize("case", sorted(QUEUE_CASES))
def test_handler_runs_and_queues_where_expected(handler, case):
    ctx, runs, group = QUEUE_CASES[case]
    assert bool(_eval(handler["if"], ctx)) is runs
    got = _eval(handler["concurrency"]["group"], ctx)
    if runs:
        assert got == group
    else:
        assert (
            got == "claude-unqueued-7001"
        ), "a skipped run must get a per-run group, so it can't displace a pending one"


def test_mentions_and_follow_ups_never_share_a_queue(handler):
    group = handler["concurrency"]["group"]
    mention = _eval(group, QUEUE_CASES["mention"][0])
    follow_up = _eval(group, QUEUE_CASES["follow-up"][0])
    assert mention != follow_up
    # A comment can't be both: the gate only runs for comments without @claude.
    both = _event("issue_comment", "created", "@claude hi", engaged="true")
    assert _eval(group, both) == "claude-mention-42"


def test_queue_never_cancels_a_running_reply(handler):
    assert handler["concurrency"]["cancel-in-progress"] is False


def test_handler_posts_through_the_workflow_with_the_marker(handler, gate):
    with_ = handler["with"]
    assert with_["comment_file"] == "/tmp/reply.md"
    marker = gate["steps"][0]["env"]["CLAUDE_REPLY_MARKER"]
    assert with_["comment_marker"] == marker
    # An HTML comment with a plain slug inside stays invisible when rendered.
    inner = marker.removeprefix("<!-- ").removesuffix(" -->")
    assert (
        inner != marker and inner and set(inner) <= set("abcdefghijklmnopqrstuvwxyz-")
    )
    assert "/tmp/reply.md" in with_["prompt"]
    assert "write NO file" in _flat(with_["prompt"])


def test_handler_scope_is_unchanged(handler):
    """Wider triggers must not come with wider rights: fork PRs reach this job."""
    assert handler["permissions"] == {
        "contents": "read",
        "issues": "write",
        "pull-requests": "write",
    }
    assert handler["with"]["allowed_non_write_users"] == "*"


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

TRIGGER_ID = 999
# The stub plays gh's --jq output: one "<id> <kind> <epoch>" line per comment.
_GH_STUB = """#!/bin/sh
echo "$*" >> "$STUB_LOG"
if [ -n "$STUB_FAIL" ]; then echo "gh: HTTP 502" >&2; exit 1; fi
cat "$STUB_COMMENTS"
"""


def _thread(*earlier, trigger=True, after=()):
    """(kind, minutes_ago) pairs, oldest first; the trigger comment is appended."""
    now = int(time.time())
    rows = [(100 + i, kind, now - mins * 60) for i, (kind, mins) in enumerate(earlier)]
    if trigger:
        rows.append((TRIGGER_ID, "member", now))
    rows += [(2000 + i, kind, now) for i, kind in enumerate(after)]
    return "".join(f"{i} {kind} {ts}\n" for i, kind, ts in rows)


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
        _thread(("claude", 10)) if comments is None else comments,
        encoding="utf-8",
        newline="\n",
    )
    out = tmp_path / "out"
    log = tmp_path / "gh.log"
    (step,) = gate["steps"]
    env = {
        **os.environ,
        **{k: str(v) for k, v in step["env"].items() if "${{" not in str(v)},
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


def test_unlabelled_pr_is_not_engaged_even_right_after_claude(gate, tmp_path):
    res, out, calls = _run_gate(gate, tmp_path, is_pr="true")
    assert res.returncode == 0, res.stderr
    assert out.strip() == "engaged=false"
    assert calls == []


def test_issue_comment_right_after_claude_is_engaged(gate, tmp_path):
    feed = _thread(("other", 90), ("claude", 60))
    res, out, calls = _run_gate(gate, tmp_path, comments=feed)
    assert res.returncode == 0, res.stderr
    assert out.strip() == "engaged=true"
    assert sum(c.startswith("api ") for c in calls) == 1


@pytest.mark.parametrize(
    "earlier",
    [
        (),  # Claude never replied
        (("claude", 600), ("other", 300)),  # someone else spoke after Claude
        (("claude", 600), ("member", 300)),  # a second maintainer comment in a row
        (("other", 5),),  # triage / audit / auto-fix: a bot comment without the marker
    ],
    ids=["no-reply", "outsider-between", "member-between", "non-claude-bot"],
)
def test_issue_comment_not_answering_claude_is_not_engaged(gate, tmp_path, earlier):
    res, out, _ = _run_gate(gate, tmp_path, comments=_thread(*earlier))
    assert res.returncode == 0, res.stderr
    assert out.strip() == "engaged=false"
    assert "does not directly follow" in res.stdout


def test_trigger_missing_from_the_listing_fails_closed(gate, tmp_path):
    feed = _thread(("claude", 5), trigger=False)
    _, out, _ = _run_gate(gate, tmp_path, comments=feed)
    assert out.strip() == "engaged=false"


def test_later_comments_do_not_change_what_the_trigger_follows(gate, tmp_path):
    feed = _thread(("claude", 5), after=("other", "claude"))
    _, out, _ = _run_gate(gate, tmp_path, comments=feed)
    assert out.strip() == "engaged=true"


def test_api_failure_fails_the_gate_loudly(gate, tmp_path):
    res, out, _ = _run_gate(gate, tmp_path, fail="1")
    assert res.returncode != 0
    assert "engaged=" not in out
    assert "::error::" in res.stdout and "#1394" in res.stdout


# --- per-thread cap on the gate's own passes ---------------------------------


def test_cap_is_small(cap):
    assert 1 <= cap <= 5


def _labelled_passes(n, start=50):
    return [("member", start - i) for i in range(n)]


def _issue_passes(n, start=50):
    rows = []
    for i in range(n):
        rows += [("claude", start - 2 * i), ("member", start - 2 * i - 1)]
    return rows


def test_cap_counts_passes_even_when_claude_stays_silent(gate, tmp_path, cap):
    """Labelled thread, no Claude replies at all: the (cap+1)th comment is declined."""
    feed = _thread(*_labelled_passes(cap))
    res, out, _ = _run_gate(gate, tmp_path, labelled="true", comments=feed)
    assert res.returncode == 0, res.stderr
    assert out.strip() == "engaged=false"
    assert "last hour" in res.stdout


def test_just_below_the_cap_is_engaged(gate, tmp_path, cap):
    feed = _thread(*_labelled_passes(cap - 1))
    _, out, _ = _run_gate(gate, tmp_path, labelled="true", comments=feed)
    assert out.strip() == "engaged=true"


def test_cap_on_an_issue_thread(gate, tmp_path, cap):
    feed = _thread(*_issue_passes(cap), ("claude", 1))
    res, out, _ = _run_gate(gate, tmp_path, comments=feed)
    assert out.strip() == "engaged=false"
    assert "last hour" in res.stdout


def test_cap_ignores_comments_the_gate_would_have_declined(gate, tmp_path, cap):
    """Unlabelled issue: maintainer comments that did not follow Claude never passed."""
    declined = [("member", 40 - i) for i in range(cap * 3)]
    feed = _thread(("other", 50), *declined, ("claude", 1))
    _, out, _ = _run_gate(gate, tmp_path, comments=feed)
    assert out.strip() == "engaged=true"


def test_passes_older_than_an_hour_do_not_count(gate, tmp_path, cap):
    feed = _thread(*_labelled_passes(cap * 3, start=200))
    _, out, _ = _run_gate(gate, tmp_path, labelled="true", comments=feed)
    assert out.strip() == "engaged=true"


# --- the jq filter that classifies comments ---------------------------------


def test_jq_filter_classifies_comments(gate):
    """Run the gate's real jq filter over a comments page."""
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq not available")
    step = gate["steps"][0]
    m = re.search(r"--jq '(.*?)'", step["run"], re.S)
    assert m, "gate lost its --jq filter"
    marker = step["env"]["CLAUDE_REPLY_MARKER"]
    bot = {"login": "github-actions[bot]", "type": "Bot"}
    human = {"login": "amd-pworfolk", "type": "User"}
    ts = "2026-06-04T16:56:10Z"

    def c(i, user, assoc, body):
        return {
            "id": i,
            "user": user,
            "author_association": assoc,
            "body": body,
            "created_at": ts,
        }

    page = [
        c(1, bot, "CONTRIBUTOR", f"Here is the answer.\n\n{marker}\n"),
        c(2, bot, "CONTRIBUTOR", "## Skill audit"),  # another bot job
        c(3, human, "MEMBER", "Thanks, that worked."),
        c(4, human, "MEMBER", f"pasting {marker} does not make me Claude"),
        c(5, human, "MEMBER", "@claude please look"),  # the mention path owns it
        c(6, human, "NONE", "outsider"),
        c(7, human, "COLLABORATOR", None),  # an empty body is not a mention
        c(8, {"login": "renovate[bot]", "type": "Bot"}, "MEMBER", "bump"),
    ]
    res = subprocess.run(
        [jq, "-r", m.group(1)],
        input=json.dumps(page),
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "CLAUDE_REPLY_MARKER": marker},
    )
    epoch = int(datetime(2026, 6, 4, 16, 56, 10, tzinfo=timezone.utc).timestamp())
    kinds = ["claude", "other", "member", "member", "other", "other", "member", "other"]
    assert res.stdout.splitlines() == [
        f"{i} {k} {epoch}" for i, k in enumerate(kinds, start=1)
    ]


# --- claude-run.yml appends the marker, not the model ------------------------


def test_post_step_appends_the_marker_and_targets_the_thread(tmp_path):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    runner = yaml.safe_load(RUNNER.read_text(encoding="utf-8"))
    inputs = runner[True]["workflow_call"]["inputs"]  # PyYAML reads `on:` as True
    assert inputs["comment_marker"]["default"] == ""
    post = next(
        s
        for s in runner["jobs"]["run"]["steps"]
        if s.get("name") == "Post Claude's comment"
    )
    assert post["env"]["COMMENT_MARKER"] == "${{ inputs.comment_marker }}"
    assert (
        "github.event.issue.pull_request != null" in post["env"]["IS_PR"]
    ), "an @claude reply in a PR conversation must post with `gh pr comment`"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "gh"
    stub.write_text(
        '#!/bin/sh\necho "$*" >> "$STUB_LOG"\n', encoding="utf-8", newline="\n"
    )
    stub.chmod(0o755)
    body = tmp_path / "reply.md"
    body.write_text("Answer.", encoding="utf-8", newline="\n")
    log = tmp_path / "gh.log"
    marker = "<!-- gaia-claude-reply -->"
    res = subprocess.run(
        [bash, "-c", post["run"]],
        env={
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "STUB_LOG": str(log),
            "COMMENT_FILE": str(body),
            "COMMENT_MARKER": marker,
            "REQUIRE_COMMENT": "false",
            "HARNESS_STATUS": "ok",
            "IS_PR": "false",
            "NUMBER": "1394",
            "RUN_URL": "https://example.invalid/run",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert body.read_text(encoding="utf-8").rstrip().endswith(marker)
    assert log.read_text(encoding="utf-8").startswith("issue comment 1394 --body-file")


def test_silence_posts_nothing_and_adds_no_marker(tmp_path):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    runner = yaml.safe_load(RUNNER.read_text(encoding="utf-8"))
    post = next(
        s
        for s in runner["jobs"]["run"]["steps"]
        if s.get("name") == "Post Claude's comment"
    )
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "gh"
    stub.write_text(
        '#!/bin/sh\necho "$*" >> "$STUB_LOG"\n', encoding="utf-8", newline="\n"
    )
    stub.chmod(0o755)
    log = tmp_path / "gh.log"
    body = tmp_path / "reply.md"
    res = subprocess.run(
        [bash, "-c", post["run"]],
        env={
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "STUB_LOG": str(log),
            "COMMENT_FILE": str(body),
            "COMMENT_MARKER": "<!-- gaia-claude-reply -->",
            "REQUIRE_COMMENT": "false",
            "HARNESS_STATUS": "ok",
            "IS_PR": "false",
            "NUMBER": "1394",
            "RUN_URL": "https://example.invalid/run",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert not body.exists() and not log.exists()
