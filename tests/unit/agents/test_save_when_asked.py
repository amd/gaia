# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
""" "Please remember this" answered with "noted" and no save is sent back once."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.memory import (
    SAVE_WHEN_ASKED_PROMPT,
    MemoryMixin,
    asks_to_keep_in_memory,
)
from gaia.agents.base.tools import tool

# rc17 smoke eval, memory_store_and_recall.
RC17 = "My name is Jordan and I'm a principal ML engineer at AMD. Please remember this."


@pytest.mark.parametrize(
    "text",
    [
        RC17,
        "Remember that my daughter's name is Mia.",
        "Can you remember that my flight is on the 14th?",
        "Don't forget I'm allergic to peanuts.",
        "I prefer metric units, keep that in mind.",
        "Please make a note: the standup moved to 9:30.",
        "Memorize this: our office is on floor 7.",
        "Save this to your memory: I like tea.",
        "Add to your memory that I like tea.",
        "I work at AMD, remember that.",
        "Here is my profile.\n\nName: Jordan\n\nPlease remember this.",
    ],
)
def test_requests_to_keep_something(text):
    assert asks_to_keep_in_memory(text)


@pytest.mark.parametrize(
    "text",
    [
        "What do you remember about me?",
        "Can you remember what I said about the budget?",
        "Remember when we talked about the launch?",
        "I don't remember the date of the meeting.",
        "Save this as notes.md in my project folder.",
        "Remind me to call mom at 5.",
        "Remember to water the plants on Friday.",
        "Summarize this report.",
        "Fix the failing test. Note that the suite uses pytest.",
        "Please note down the meeting summary in notes.md",
        "Please make a note in notes.md that we shipped.",
        "Can you remember my dog's name?",
        "So, remember the Python script from before? Run it again.",
        "Translate this letter:\n\nDear Sam, please remember that rent is due.",
        "What does 'remember me' do? Remember me checkboxes confuse people.",
        "Summarize this log. " + "x " * 400 + "please remember this",
    ],
)
def test_questions_files_and_reminders_are_not(text):
    assert not asks_to_keep_in_memory(text)


def _memory(text, registry=("remember", "recall"), incognito=False, background=False):
    host = SimpleNamespace(
        _tools_registry=dict.fromkeys(registry),
        _original_user_input=text,
        _incognito=incognito,
        console=SimpleNamespace(background_mode=background),
        _looks_like_credential=MemoryMixin._looks_like_credential,
    )
    return lambda called: MemoryMixin._unsaved_memory_request(host, called)


def test_the_nudge_goes_only_when_nothing_was_saved():
    unsaved = _memory(RC17)
    assert unsaved(set()) == SAVE_WHEN_ASKED_PROMPT
    assert unsaved({"recall"}) == SAVE_WHEN_ASKED_PROMPT
    assert unsaved({"remember"}) is None
    assert unsaved({"update_memory"}) is None


def test_no_nudge_without_memory_or_for_a_secret():
    assert _memory(RC17, registry=("recall",))(set()) is None
    assert _memory("Please remember my password is hunter2-Xq9!")(set()) is None
    assert _memory("Summarize this report.")(set()) is None


def test_no_nudge_in_a_private_chat():
    assert _memory(RC17, incognito=True)(set()) is None


def test_no_nudge_in_a_background_run():
    assert _memory(RC17, background=True)(set()) is None


# ---------------------------------------------------------------------------
# Loop integration with a scripted fake LLM
# ---------------------------------------------------------------------------


class _RememberingAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "You are a test agent."

    def _register_tools(self) -> None:
        agent = self
        agent.saved = []

        @tool
        def remember(fact: str) -> dict:
            """Save a fact."""
            agent.saved.append(fact)
            return {"status": "success", "message": "Saved."}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()

    def _unsaved_memory_request(self, tools_called):
        if "remember" in tools_called:
            return None
        return SAVE_WHEN_ASKED_PROMPT


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _RememberingAgent(silent_mode=True, skip_lemonade=True)
        a.streaming = False
        return a


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


def _answer(text):
    return json.dumps({"thought": "", "answer": text})


def _call(tool, **args):
    return json.dumps({"thought": "", "tool": tool, "tool_args": args})


def test_noted_without_a_save_is_sent_back_once(agent):
    sent = _script(
        agent,
        _answer("Got it, Jordan. Principal ML engineer at AMD — noted."),
        _call("remember", fact="Jordan is a principal ML engineer at AMD"),
        _answer("Saved: you're Jordan, a principal ML engineer at AMD."),
    )

    agent.process_query(RC17, max_steps=10)

    assert agent.saved == ["Jordan is a principal ML engineer at AMD"]
    nudges = [
        m
        for m in sent[1]
        if m.get("role") == "user" and m["content"] == SAVE_WHEN_ASKED_PROMPT
    ]
    assert len(nudges) == 1


def test_the_nudge_is_sent_only_once(agent):
    sent = _script(
        agent,
        _answer("Noted."),
        _answer("Noted, Jordan."),
    )

    result = agent.process_query(RC17, max_steps=10)

    assert len(sent) == 2
    assert agent.saved == []
    assert "Noted, Jordan." in result["result"]
