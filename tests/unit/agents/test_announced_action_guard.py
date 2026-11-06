# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A final answer that only announces its next action ("I will perform one
final search…") is re-prompted, and never handed to the user as the answer.\n\nSeen on Gemma 4 E4B (NPU) in v0.25.4-rc13 and in the ai-pc-evals data tasks\n(amd/gaia#6753, thread 1).\n
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import (
    _MAX_UNFINISHED_ANSWER_REPROMPTS,
    Agent,
    _unfinished_answer_kind,
)
from gaia.agents.base.verification import strip_verification_scope

LEMONADE_ANNOUNCEMENT = (
    "The Lemonade Server releases page lists v10.0.1 as the newest tag. "
    "Before finalizing, I will perform one final targeted search on the web "
    "to see if there is a more current announcement"
)

ANNOUNCED_ACTIONS = [
    pytest.param(LEMONADE_ANNOUNCEMENT, id="rc13-lemonade-final-search"),
    pytest.param(
        "The CSV has 1,204 rows across 12 months. I'll run the trend analysis now.",
        id="pc-evals-trend-analysis",
    ),
    pytest.param(
        "I found the sales file. Next, I'll compute the monthly totals.",
        id="next-ill",
    ),
    pytest.param("I will now check the release notes.", id="i-will-now"),
    pytest.param(
        "Got the page. I'm going to fetch the changelog to confirm.",
        id="im-going-to",
    ),
    pytest.param("Next, let me open the second report.", id="next-let-me"),
    pytest.param(
        "I've read the first PDF. "
        + "It covers the Q2 results in detail. " * 20
        + "Next, I will perform the same extraction on the second PDF.",
        id="long-paragraph",
    ),
    pytest.param(
        "The search returned nothing useful. I'll try a different query.",
        id="ill-try",
    ),
    pytest.param(
        "I have the list of files. I'll read the config next.",
        id="trailing-next",
    ),
    pytest.param(
        "The build log shows one warning. Then I'll fetch the full log to confirm.",
        id="then-ill-no-comma",
    ),
    # A second "I'll" must not pass for a plan when the last step is this turn's.
    pytest.param(
        "I'll check the releases page now, then I'll summarize what I find.",
        id="two-actions-now",
    ),
    pytest.param(
        "I'll summarize what I found below. Before finalizing, I will perform "
        "one final search to confirm the date.",
        id="promise-then-rc13",
    ),
    pytest.param(
        "I'll keep this short. I'll check the releases page now.",
        id="promise-then-step-now",
    ),
    pytest.param("We'll now check the logs to find the error.", id="well-now"),
    pytest.param("Let's check the logs now.", id="lets-now"),
]


@pytest.mark.parametrize("answer", ANNOUNCED_ACTIONS)
def test_announced_actions_are_narration(answer):
    assert _unfinished_answer_kind(answer) == "narration"


REAL_ANSWERS = [
    pytest.param(
        "Your meeting is moved to Thursday at 3 PM. I'll keep that in mind.",
        id="keep-in-mind",
    ),
    pytest.param(
        "The latest release is v10.0.1. Want me to check the changelog too?",
        id="want-me-to",
    ),
    pytest.param(
        "Total revenue was $48,210. If you'd like, I'll run the trend analysis "
        "as well.",
        id="if-youd-like-offer",
    ),
    pytest.param(
        "Saved. I'll remember that you prefer metric units.",
        id="ill-remember",
    ),
    pytest.param(
        "Done. Next time, I'll check the cache first.",
        id="next-time",
    ),
    pytest.param(
        "The build is green. Once you confirm, I'll run the release script.",
        id="once-you-confirm",
    ),
    pytest.param(
        "I can't reach that site. I'll check again later if you ask.",
        id="later",
    ),
    pytest.param("Should I run the trend analysis now?", id="question"),
    pytest.param(
        "Thanks for the details. I'll look forward to the next report.",
        id="look-forward",
    ),
    pytest.param(
        "Revenue grew 12% in Q3. I'll be happy to break it down by region.",
        id="ill-be-happy",
    ),
    # Drafted content speaks in the user's voice: its closing commitment is
    # part of the answer.
    pytest.param(
        "Hi Sam,\n\nThanks for the contract. I will review it and send comments "
        "by Friday.",
        id="email-draft-no-signoff",
    ),
    pytest.param(
        "Here's a draft reply:\n\nHi Priya,\n\nThanks for flagging the outage. "
        "I'll check with the infra team and get back to you.\n\nBest,\nAlex",
        id="email-draft-signoff",
    ),
    pytest.param(
        "Meeting notes - Q4 planning\n\n- Budget approved\n- Launch moved to "
        "Dec 2\n\nAs the owner, I will create the Jira tickets and share the "
        "timeline.",
        id="action-item-lead-clause",
    ),
    pytest.param(
        "Your goals for next week:\n1. Finish the slides\n2. I will review the "
        "budget with finance",
        id="list-item",
    ),
    pytest.param(
        "Your PR is merged. I will continue to monitor the CI.", id="continue-to"
    ),
    pytest.param(
        "Slack reply draft:\n\nThanks all! I'll review the PR this afternoon.",
        id="this-afternoon",
    ),
    pytest.param("We will review the results in the next sprint.", id="next-sprint"),
    pytest.param(
        "The tests pass on Linux and macOS. I will test it again on the next RC.",
        id="next-rc",
    ),
    pytest.param(
        "I've restarted the service and it is healthy. If the error returns, "
        "I'll check the logs.",
        id="conditional-lead",
    ),
    pytest.param(
        "I'll start with the summary: sales are up 8%, mostly from the East "
        "region, and returns fell.",
        id="announces-then-answers",
    ),
    pytest.param(
        "Dear Hiring Manager,\n\n"
        + "In my current role I built dashboards that cut reporting time by 40%. " * 12
        + "I will review your roadmap closely.\n\nSincerely,\nJordan",
        id="long-cover-letter",
    ),
    # After a denied run, an honest answer waits on the user; pushing it to
    # "do it now" would retry the declined step.
    pytest.param(
        "I'll compute the totals as soon as you allow the Python run.",
        id="denied-as-soon-as",
    ),
    pytest.param(
        "I'll run the analysis once the Python step is approved.",
        id="denied-once-approved",
    ),
    pytest.param(
        "I can't compute this without running code. I'll calculate it if you "
        "approve the run.",
        id="denied-if-you-approve",
    ),
    pytest.param(
        "Running the calculation was declined, so I'll need your approval to "
        "compute the totals.",
        id="denied-need-approval",
    ),
    pytest.param(
        "I will check the logs after you grant access.", id="denied-after-grant"
    ),
    pytest.param(
        "Python was denied, so nothing was computed. I'll calculate the regional "
        "totals when the run is allowed.",
        id="denied-when-allowed",
    ),
    pytest.param(
        "From your notes:\n\n> I'll run the tests now.",
        id="quoted-last-line",
    ),
    # A plan or tutorial the user asked for announces its steps on purpose.
    pytest.param(
        "Here's your plan for the afternoon:\n\nFirst, I'll clear my inbox. "
        "Next, I'll review the two open PRs. Finally, I'll test the release "
        "candidate.",
        id="plan-user-asked",
    ),
    pytest.param(
        "Your afternoon plan: first, I'll clear my inbox, then I'll review the "
        "PRs. Finally, I'll test the release.",
        id="plan-one-paragraph",
    ),
    pytest.param(
        "To set up the database, install Postgres and create a user called app "
        "with a password. Then create the app database and grant the user "
        "access to it.\n\nFinally, we'll test the connection to make sure it "
        "works.",
        id="tutorial-finally-well",
    ),
    pytest.param(
        "We've created the table and inserted the sample rows. Next, we'll run "
        "the migration.",
        id="tutorial-next-well",
    ),
    pytest.param("Next, we'll run the migration.", id="next-well-alone"),
    pytest.param(
        "Install Postgres and create the app user.\n\nFinally, we need to "
        "restart the service.",
        id="tutorial-finally-we-need-to",
    ),
    pytest.param(
        "Suggested commit message:\n\nfix(memory): drop stale tool errors\n\n"
        "We now retire errors from older versions. We'll re-learn real ones "
        "next time they happen.",
        id="commit-message-we-now",
    ),
    pytest.param("Next, let's run the migration.", id="tutorial-lets"),
]


@pytest.mark.parametrize("answer", REAL_ANSWERS)
def test_real_answers_are_not_narration(answer):
    assert _unfinished_answer_kind(answer) is None


# ---------------------------------------------------------------------------
# Loop integration with a scripted fake LLM
# ---------------------------------------------------------------------------


class _DummyAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "You are a test agent."

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _DummyAgent(silent_mode=True, skip_lemonade=True)
        a.streaming = False
        return a


def _answer(text):
    return json.dumps({"thought": "", "answer": text})


def _call(tool, **args):
    return json.dumps({"thought": "", "tool": tool, "tool_args": args})


def _script(agent, *replies):
    replies = list(replies)
    sent = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        resp = MagicMock()
        resp.text = replies.pop(0)
        resp.stats = {}
        return resp

    agent.chat = MagicMock(send_messages=MagicMock(side_effect=_send))
    return sent


def _final_text(result):
    return strip_verification_scope(result["result"]).strip()


def _nudges(sent):
    return [
        m["content"]
        for m in sent[-1]
        if m.get("role") == "user"
        and "described your next step instead of doing it" in str(m.get("content"))
    ]


def test_announce_then_tool_call_then_answer_continues_once(agent):
    calls = []
    agent._tools_registry["release_lookup"] = {
        "function": lambda **kw: calls.append(kw) or {"latest": "v10.0.2"},
        "description": "Look up the latest release.",
        "parameters": {},
    }
    sent = _script(
        agent,
        _answer(LEMONADE_ANNOUNCEMENT),
        _call("release_lookup"),
        _answer("The latest Lemonade Server release is v10.0.2 (releases page)."),
    )

    result = agent.process_query(
        "What is the latest released version of Lemonade Server?", max_steps=10
    )

    assert len(sent) == 3
    assert calls == [{}]
    nudges = _nudges(sent)
    assert len(nudges) == 1
    assert "I will perform one final targeted search" in nudges[0]
    assert _final_text(result) == (
        "The latest Lemonade Server release is v10.0.2 (releases page)."
    )


def test_real_answer_with_an_offer_is_not_continued(agent):
    answer = "The latest release is v10.0.1. Want me to check the changelog too?"
    sent = _script(agent, _answer(answer))

    result = agent.process_query("Latest Lemonade release?", max_steps=10)

    assert len(sent) == 1
    assert _final_text(result) == answer


def test_endless_announcing_ends_honestly_at_the_cap(agent):
    sent = _script(
        agent,
        *[_answer("I'll run the trend analysis now.")] * 5,
    )

    result = agent.process_query("Show the sales trend", max_steps=20)

    assert len(sent) == _MAX_UNFINISHED_ANSWER_REPROMPTS + 1
    final = _final_text(result)
    assert final.startswith("I didn't finish:")
    assert "I'll run the trend analysis now." in final
    assert not final.endswith("I'll run the trend analysis now.")


def test_announcement_keeps_what_was_found_when_it_ends_honestly(agent):
    _script(agent, *[_answer(LEMONADE_ANNOUNCEMENT)] * 5)

    result = agent.process_query("Latest Lemonade release?", max_steps=20)

    final = _final_text(result)
    assert final.startswith("I didn't finish:")
    assert "lists v10.0.1 as the newest tag" in final


def test_announcement_on_the_last_step_ends_honestly(agent):
    sent = _script(agent, _answer("I'll run the trend analysis now."))

    result = agent.process_query("Show the sales trend", max_steps=1)

    assert len(sent) == 1
    assert _final_text(result).startswith("I didn't finish:")


def test_honest_ending_is_not_mistaken_for_planning_text(agent):
    # Once the re-prompts are spent, the honest ending quotes "I'll check…",
    # which is also a planning phrase; it must reach the user, not loop again.
    sent = _script(
        agent,
        _answer("I'll run the trend analysis now."),
        _answer("I'll run the trend analysis now."),
        *[_answer("I'll check the sales file now.")] * 6,
    )

    result = agent.process_query("Show the sales trend", max_steps=20)

    assert len(sent) == _MAX_UNFINISHED_ANSWER_REPROMPTS + 1
    final = _final_text(result)
    assert final.startswith("I didn't finish:")
    assert "I'll check the sales file now." in final


def test_planning_phrase_guard_shares_the_reprompt_budget(agent):
    # "i'll check" also trips the planning-phrase guard; it must not loop past the cap.
    sent = _script(agent, *[_answer("I'll check the releases page now.")] * 8)

    result = agent.process_query("Latest Lemonade release?", max_steps=20)

    assert len(sent) == _MAX_UNFINISHED_ANSWER_REPROMPTS + 1
    assert _final_text(result).startswith("I didn't finish:")
