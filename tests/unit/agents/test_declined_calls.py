# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A call the user declined is not put to them again in the same chat (#8806).

Seen live: ``echo hi<U+200B>there`` has no effect the effect ledger records,
so after each Deny the model retried it and the same card came back twice.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import DECLINED_HINT, Agent
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.denied_effects import DeclinedCalls, DeniedEffects
from gaia.agents.base.tools import tool

ECHO = "echo hi​there"


class _DenyShellConsole(AgentConsole):
    """Denies run_shell_command, approves everything else, records prompts."""

    def __init__(self):
        super().__init__()
        self.prompted = []

    def confirm_tool_execution(self, tool_name, tool_args):
        self.prompted.append(tool_args.get("command"))
        return tool_name != "run_shell_command"


class _Probe(Agent):
    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        agent = self
        agent.ran = []

        @tool
        def run_shell_command(command: str) -> dict:
            """Probe shell."""
            agent.ran.append(command)
            return {"status": "success", "return_code": 0}

    def _create_console(self):
        return self._console_override


def _agent(console, cwd):
    with patch("gaia.agents.base.agent.AgentSDK"):
        _Probe._console_override = console
        a = _Probe(silent_mode=True, skip_lemonade=True)
    a._denied_effects = DeniedEffects(str(cwd))
    a.streaming = False
    return a


def _shell(command):
    return {"command": command}


def _stub_chat(agent, *responses):
    queue = list(responses)
    chat = MagicMock()

    def _send(messages, *_, **__):
        if not queue:
            raise AssertionError("chat stub ran out of scripted responses")
        resp = MagicMock()
        resp.text = queue.pop(0)
        resp.stats = {}
        return resp

    chat.send_messages = MagicMock(side_effect=_send)
    agent.chat = chat


def _call(command):
    return json.dumps(
        {"thought": "next", "tool": "run_shell_command", "tool_args": _shell(command)}
    )


def _answer(text):
    return json.dumps({"thought": "done", "answer": text})


# ---------------------------------------------------------------- in one turn


def test_a_declined_command_with_no_effect_is_not_asked_about_again(tmp_path):
    console = _DenyShellConsole()
    agent = _agent(console, tmp_path)

    first = agent._execute_tool("run_shell_command", _shell(ECHO))
    second = agent._execute_tool("run_shell_command", _shell(ECHO))

    assert first["status"] == "denied"
    assert second["status"] == "denied"
    assert second["executed"] is False
    assert second["already_declined"] is True
    assert second["hint"] == DECLINED_HINT
    assert ECHO in second["error"]
    # Classed as a no, so the recovery prompt says not to retry.
    assert Agent._is_denial_error(second["error"])
    assert console.prompted == [ECHO]
    assert agent.ran == []


@pytest.mark.parametrize("respelled", [f"  {ECHO} ", "echo  hi​there", f"{ECHO}\n"])
def test_whitespace_alone_does_not_make_it_another_call(tmp_path, respelled):
    console = _DenyShellConsole()
    agent = _agent(console, tmp_path)

    agent._execute_tool("run_shell_command", _shell(ECHO))
    again = agent._execute_tool("run_shell_command", _shell(respelled))

    assert again["status"] == "denied"
    assert console.prompted == [ECHO]


@pytest.mark.parametrize(
    "other",
    [
        "echo hithere",  # the hidden character is part of the call
        "echo hi there",  # a no-break space is not ASCII whitespace
        "echo hi there",
        "ls",
    ],
)
def test_a_different_call_after_a_denial_is_still_asked_about(tmp_path, other):
    console = _DenyShellConsole()
    agent = _agent(console, tmp_path)

    agent._execute_tool("run_shell_command", _shell(ECHO))
    agent._execute_tool("run_shell_command", _shell(other))

    assert console.prompted == [ECHO, other]


def test_the_same_command_through_another_tool_is_another_call():
    declined = DeclinedCalls()
    declined.record("run_shell_command", _shell(ECHO))

    assert declined.declined("run_shell_command", _shell(ECHO)) == ECHO
    assert declined.declined("run_in_terminal", _shell(ECHO)) is None


def test_a_prompt_nobody_answered_is_not_a_no(tmp_path):
    class _Expired(_DenyShellConsole):
        def confirmation_timed_out(self, tool_name):
            return True

    agent = _agent(_Expired(), tmp_path)
    agent._execute_tool("run_shell_command", _shell(ECHO))

    assert not agent._declined_calls


def test_a_console_with_nobody_to_ask_is_not_a_no(tmp_path):
    class _Unattended(_DenyShellConsole):
        def confirmation_unattended(self, tool_name):
            return True

    agent = _agent(_Unattended(), tmp_path)
    agent._execute_tool("run_shell_command", _shell(ECHO))

    assert not agent._declined_calls


def test_no_to_the_reroute_question_covers_that_exact_call(tmp_path):
    """Saying no to the reroute question is a no to that call, asked once."""

    class _Asking(_DenyShellConsole):
        def __init__(self):
            super().__init__()
            self.questions = []

        def request_user_input_blocking(self, message, choices=None, **_):
            self.questions.append(message)
            return Agent.KEEP_DENIED_EFFECT

    console = _Asking()
    agent = _agent(console, tmp_path)
    agent._execute_tool("run_shell_command", _shell("pytest -q"))

    first = agent._execute_tool("run_shell_command", _shell("pytest -q -x"))
    again = agent._execute_tool("run_shell_command", _shell("pytest -q -x"))

    assert first["status"] == "denied" and again["status"] == "denied"
    assert again["already_declined"] is True
    assert len(console.questions) == 1


# --------------------------------------------------------- the rest of the chat


def test_the_no_lasts_into_the_next_turn(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    console = _DenyShellConsole()
    agent = _agent(console, tmp_path)

    _stub_chat(agent, _call(ECHO), _call(ECHO), _answer("You declined it."))
    agent.process_query(f"Run this exact command: {ECHO}")
    _stub_chat(agent, _call(ECHO), _answer("Not run: you declined it."))
    agent.process_query("what does that print?")

    assert console.prompted == [ECHO]
    assert agent.ran == []


@pytest.mark.parametrize(
    "asks_again",
    [
        "run it again",
        "OK, try again",
        "go ahead",
        "yes",
        f"please run {ECHO} now",
        "No, I misclicked, run it again",
        f"no wait, please run {ECHO}",
        "Run the shell command again",
        "Why didn't that work? Please try running the same command again.",
    ],
)
def test_asking_again_puts_it_to_the_user_again(tmp_path, monkeypatch, asks_again):
    monkeypatch.chdir(tmp_path)
    console = _DenyShellConsole()
    agent = _agent(console, tmp_path)

    _stub_chat(agent, _call(ECHO), _answer("You declined it."))
    agent.process_query(f"Run this exact command: {ECHO}")
    _stub_chat(agent, _call(ECHO), _answer("Declined again."))
    agent.process_query(asks_again)

    assert console.prompted == [ECHO, ECHO]


@pytest.mark.parametrize(
    "not_again",
    [
        "don't run it",
        "No, leave it",
        "do not retry",
        "no",
        "No, don't run it again",
        "No, not again",
    ],
)
def test_saying_not_to_does_not_lift_it(tmp_path, monkeypatch, not_again):
    monkeypatch.chdir(tmp_path)
    console = _DenyShellConsole()
    agent = _agent(console, tmp_path)

    _stub_chat(agent, _call(ECHO), _answer("You declined it."))
    agent.process_query(f"Run this exact command: {ECHO}")
    _stub_chat(agent, _call(ECHO), _answer("Not run."))
    agent.process_query(not_again)

    assert console.prompted == [ECHO]


#: Ordinary ways people ask for a declined call again. Before chat-long
#: declines, each of these got a fresh approval card; none may get a refusal.
ORDINARY_REASKS = [
    "try it once more",
    "one more time please",
    "can you try that",
    "redo it",
    "please execute it",
    "ok, proceed",
    "sorry, I misclicked",
    "I changed my mind, run it",
    "Why not run it again?",
    "No, don’t stop, try it again",
    "otra vez por favor",
    "inténtalo de nuevo",
    "réessaie",
    "encore une fois",
    "nochmal bitte",
    "noch einmal",
    "di nuovo",
    "riprova",
    "tente novamente",
    "opnieuw",
    "ещё раз",
    "попробуй снова",
    "再试一次",
    "重新运行",
    "もう一度",
    "다시 해줘",
    "مرة أخرى",
    "फिर से चलाओ",
]

#: Ordinary follow-ups that don't ask for the call; the no must still hold.
ORDINARY_NON_REASKS = [
    "what does that print?",
    "Hmm, ok. What did you try to run?",
    "Why did it fail?",
    "thanks",
    "what else can you do?",
    "No, don’t run it again",
    "never try that",
    "summarize what happened",
]


@pytest.mark.parametrize("message", ORDINARY_REASKS)
def test_ordinary_reasks_lift_the_no(message):
    declined = DeclinedCalls()
    declined.record("run_shell_command", _shell(ECHO))

    assert declined.lift_for(message) == [ECHO]


@pytest.mark.parametrize("message", ORDINARY_NON_REASKS)
def test_ordinary_follow_ups_keep_the_no(message):
    declined = DeclinedCalls()
    declined.record("run_shell_command", _shell(ECHO))

    assert declined.lift_for(message) == []
    assert declined.declined("run_shell_command", _shell(ECHO)) == ECHO


@pytest.mark.parametrize("message", ["try it once more", "nochmal bitte", "再试一次"])
def test_an_ordinary_reask_gets_the_approval_card_again(tmp_path, monkeypatch, message):
    monkeypatch.chdir(tmp_path)
    console = _DenyShellConsole()
    agent = _agent(console, tmp_path)

    _stub_chat(agent, _call(ECHO), _answer("You declined it."))
    agent.process_query(f"Run this exact command: {ECHO}")
    _stub_chat(agent, _call(ECHO), _answer("Declined again."))
    agent.process_query(message)

    assert console.prompted == [ECHO, ECHO]


def test_naming_one_call_lifts_only_that_one():
    declined = DeclinedCalls()
    declined.record("run_shell_command", _shell(ECHO))
    declined.record("run_shell_command", _shell("ls -la"))

    assert declined.lift_for("what about ls -la?") == ["ls -la"]
    assert declined.declined("run_shell_command", _shell(ECHO)) == ECHO
    assert declined.declined("run_shell_command", _shell("ls -la")) is None


def test_a_follow_up_during_the_turn_can_ask_again(tmp_path):
    console = _DenyShellConsole()
    agent = _agent(console, tmp_path)
    agent._execute_tool("run_shell_command", _shell(ECHO))

    Agent._lift_declined_calls(agent, "actually, go ahead")
    agent._execute_tool("run_shell_command", _shell(ECHO))

    assert console.prompted == [ECHO, ECHO]


def test_a_change_only_in_indentation_is_another_call():
    declined = DeclinedCalls()
    before = {"path": "a.py", "content": "if x:\n  y()\n"}
    declined.record("write_file", before)

    assert declined.declined("write_file", before) is not None
    assert (
        declined.declined("write_file", {**before, "content": "if x:\n    y()\n"})
        is None
    )
    assert declined.declined("write_file", {**before, "content": "if x: y()"}) is None
