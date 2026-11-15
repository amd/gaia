# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The request gate: asked to run a command, the agent must not answer with
output it never produced (#5735).

On v0.25.1-rc11 with local Qwen3.6, "Use your shell tool to run echo %TIME%"
was answered "13:42:05.89" at real time 20:42 with no tool call, and "run pwd"
was answered from the cwd in the system prompt.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.grounding import REQUEST, request_findings, ungrounded
from gaia.agents.base.tools import tool
from gaia.agents.base.verification import (
    VERIFICATION_NOTE_OPENER,
    strip_verification_scope,
)

SHELL_RAN = [{"tool": "run_shell_command", "ran": True}]
SHELL_DENIED = [{"tool": "run_shell_command", "ran": False}]


@pytest.mark.parametrize(
    "query, answer",
    [
        (
            "Use your shell tool to run echo %TIME% and tell me exactly what it printed.",
            "13:42:05.89",
        ),
        ("Use your shell tool to run pwd and tell me the directory.", "C:/Users/me"),
        ("Run the tests and tell me if they pass", "All 12 passed."),
        ("Please run git status", "Working tree clean."),
        ("Run `npm test` and report the result", "All 30 passed."),
        ("Using the terminal, list the files here", "a.txt b.txt"),
        # Everyday wordings that named no known program right after "run".
        ("Run this command: gh auth status", "Logged in to github.com"),
        ("Run exactly this command: hostname", "DESKTOP-1"),
        ("Run exactly: whoami", "me"),
        ("Please run the following command: git status", "Working tree clean."),
        ("Run this: npm test", "All 30 passed."),
        ("Can you run `git status`?", "Working tree clean."),
        ("Could you run this command: pytest -q", "12 passed"),
        ("Can you run cmdkey /list", "Currently stored credentials: none"),
        ("Run cmdkey /list", "Currently stored credentials: none"),
        ("In the shell, run: dir", "a.txt b.txt"),
        (
            'Use run_shell_command once with this exact command: cmd /c "set GH_"',
            "GH_=",
        ),
        # Other ways in that must still be caught.
        ("OK, run git status", "Working tree clean."),
        ("Could you please run `ls`", "a.txt b.txt"),
        ("Will you run this: dir", "a.txt b.txt"),
        ("Can you run python -m pytest?", "12 passed"),
        ("Run ipconfig /all", "IPv4 Address: 10.0.0.2"),
        ("In the terminal: run whoami", "me"),
        ("In your shell, please run dir", "a.txt b.txt"),
        ("From the command line, execute ver", "Microsoft Windows 11"),
    ],
)
def test_an_answer_to_a_run_request_with_nothing_run_is_flagged(query, answer):
    [finding] = request_findings(answer, query, [])

    assert finding.gate == REQUEST
    assert "Run it now" in finding.correction


@pytest.mark.parametrize(
    "query, answer, records",
    [
        # It ran: the answer may be the output.
        ("Use your shell tool to run pwd", "C:/Users/me", SHELL_RAN),
        # Questions about running things are not requests to run them.
        ("How do I run the tests in this repo?", "Use pytest.", []),
        ("What does the run command do?", "It starts the app.", []),
        # Saying it didn't run is honest.
        ("Use your shell tool to run rm -rf /", "I can't run that command.", []),
        ("Run the tests", "I didn't run them: you declined the prompt.", SHELL_DENIED),
        # Not commands: the right tool answered, or none was needed (PR review).
        (
            "Run a web search for AMD news today",
            "AMD announced…",
            [{"tool": "search_web", "ran": True}],
        ),
        (
            "Run a query on my sales table for the total",
            "$52.47",
            [{"tool": "query_data", "ran": True}],
        ),
        (
            "Execute the plan we discussed",
            "Done, files written.",
            [{"tool": "write_file", "ran": True}],
        ),
        ("Run me through how RAG works", "RAG retrieves…", []),
        ("Can you run me through this file?", "It defines…", []),
        ("Now run it again", "Done.", []),
        ("Run the numbers on Q3 / Q4 revenue", "Q4 is up 4%.", []),
        ("Runtime: what is it?", "The time a program takes.", []),
        # Questions about ability are not orders (PR review).
        ("Can you run Python scripts?", "Yes, I can.", []),
        ("Can you run docker containers on this PC?", "Yes.", []),
        ("Can you run git commands, or only read files?", "Both.", []),
        ("Could you run the tests if I asked?", "Yes.", []),
        (
            "How do I install it from the terminal? Can I run it after?",
            "Use pip, then yes.",
            [],
        ),
        ("In the terminal, can I run things?", "Yes.", []),
        ("In your terminal you can run it later", "Got it.", []),
        ("In a bash script, run loops?", "Use for.", []),
        # A switch after a word that is not a program.
        ("Run the -v flag past me again", "It means verbose.", []),
        ("Run me -through the steps", "First…", []),
        ("Run it /now", "Done.", []),
        ("Can you run Windows / Linux apps?", "Yes.", []),
        ("Can you run 24/7?", "Yes.", []),
        ("Can you run faster than me: yes or no?", "No.", []),
        # Honest refusals in other words.
        ("Use your shell tool to run pwd", "I haven't run it: no shell here.", []),
        (
            "Use your shell tool to run pwd",
            "I don't have a shell tool in this chat.",
            [],
        ),
        ("Use your shell tool to run pwd", "I'm not able to execute commands.", []),
        # A pasted document's "Run ..." line is not the user's request.
        (
            "Summarize this README:\nRun npm install, then npm start.",
            "It installs and starts the app.",
            [],
        ),
    ],
)
def test_nothing_is_flagged_when_it_ran_or_was_not_asked(query, answer, records):
    assert request_findings(answer, query, records) == []


@pytest.mark.parametrize(
    "query",
    [
        "please " * 20000 + "tell me",
        "run" + " " * 50000 + "?",
        "In the shell " * 10000,
        "Can you run " + "a" * 100000,
        "Run this " + " " * 50000,
        "ok, " * 20000,
    ],
    ids=["please", "spaces", "shell", "can-you", "this", "ok"],
)
def test_long_input_is_checked_quickly(query):
    start = time.perf_counter()
    request_findings("Done.", query, [])
    assert time.perf_counter() - start < 1.0


def test_the_gate_reaches_the_loops_check():
    findings = ungrounded(
        "13:42:05.89",
        "Use your shell tool to run echo %TIME% and tell me what it printed.",
        [],
    )

    assert REQUEST in {f.gate for f in findings}


# ---------------------------------------------------------------------------
# Through the real loop, with a scripted model
# ---------------------------------------------------------------------------


TIME_QUERY = (
    "Use your shell tool to run echo %TIME% and tell me exactly what it printed."
)
REAL_TIME = "20:42:17.03"
MADE_UP = "It printed: 13:42:05.89"


class _ShellAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def run_command_fixture(command: str) -> dict:
            """Run a shell command."""
            return {"status": "success", "command": command, "stdout": REAL_TIME}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def shell_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _ShellAgent(silent_mode=True, skip_lemonade=True)
        a.streaming = False
        yield a


def _script(agent_, *responses):
    queue = list(responses)
    sent = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        resp = MagicMock()
        resp.text = queue.pop(0)
        resp.stats = {}
        return resp

    agent_.chat = MagicMock()
    agent_.chat.send_messages = MagicMock(side_effect=_send)
    return sent


def _answer(text: str) -> str:
    return json.dumps({"thought": "done", "answer": text})


def _run_call() -> str:
    return json.dumps(
        {
            "thought": "running",
            "tool": "run_command_fixture",
            "tool_args": {"command": "echo %TIME%"},
        }
    )


def test_a_made_up_output_is_sent_back_once_and_the_real_one_ships(shell_agent):
    sent = _script(
        shell_agent, _answer(MADE_UP), _run_call(), _answer(f"It printed: {REAL_TIME}")
    )

    result = shell_agent.process_query(TIME_QUERY, max_steps=10)

    assert len(sent) == 3
    correction = sent[1][-1]["content"]
    assert correction.startswith("[check:grounding]")
    assert "ran nothing this turn" in correction
    assert (
        strip_verification_scope(result["result"]).strip() == f"It printed: {REAL_TIME}"
    )


def test_made_up_output_twice_ships_marked_not_confirmed(shell_agent):
    sent = _script(shell_agent, _answer(MADE_UP), _answer(MADE_UP))

    result = shell_agent.process_query(TIME_QUERY, max_steps=10)

    assert len(sent) == 2, "the gate corrects once, never loops"
    assert VERIFICATION_NOTE_OPENER in result["result"]
    assert "I was asked to run a command and ran nothing" in result["result"]
