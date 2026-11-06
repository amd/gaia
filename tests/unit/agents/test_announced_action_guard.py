# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A final answer that only announces its next action ("I will perform one
final search…") is re-prompted, and never handed to the user as the answer.

Seen on Gemma 4 E4B (NPU) in v0.25.4-rc13 and in the ai-pc-evals data tasks
(amd/gaia#6753, thread 1).
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
        + "I will perform the same extraction on the second PDF.",
        id="long-paragraph",
    ),
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
