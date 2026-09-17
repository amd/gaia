# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The output guards have to fire *inside the loop*, not just match a regex.

The regexes were unit-tested and passed; the benchmark kept failing the exact
tasks they were written for. Testing the pattern proves the pattern. These
tests drive the real ``process_query`` loop with a scripted model and assert on
what the loop did, which is the only thing the benchmark measures.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.tools import tool


class _Reply:
    """What ``AgentSDK.send_messages`` hands back."""

    def __init__(self, text):
        self.text = text
        self.stats = {}


class _ScriptedAgent(Agent):
    """Agent whose model says exactly what the script says, in order.

    Exposes ``write_file`` so a turn can genuinely produce an artefact, which
    is what separates "the guard re-prompted" from "the guard fixed it".
    """

    def _get_system_prompt(self) -> str:
        return "test"

    def _create_console(self):
        # write_file is confirmation-gated and a silent console has nobody to
        # ask, so without pre-approval the guard's re-prompt lands on a refusal
        # and the test measures the console, not the guard. Same consent the
        # benchmark grants via GAIA_AUTO_APPROVE_TOOLS=1.
        return SilentConsole(auto_approve_gated_tools=True)

    def _register_tools(self) -> None:
        @tool
        def write_file(path: str, content: str) -> dict:
            """Write *content* to *path*."""
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            return {"status": "success", "path": path}

        @tool
        def read_file(path: str) -> dict:
            """Read *path*."""
            with open(path, encoding="utf-8") as fh:
                return {"status": "success", "content": fh.read()}


def _answer(text):
    return json.dumps({"answer": text})


def _call(name, **args):
    return json.dumps({"tool": name, "tool_args": args})


@pytest.fixture
def scripted(tmp_path, monkeypatch):
    """Build an agent in *tmp_path* driven by a scripted response list."""
    monkeypatch.chdir(tmp_path)

    def build(script):
        with patch("gaia.agents.base.agent.AgentSDK") as sdk:
            chat = MagicMock()
            chat.send_messages.side_effect = [_Reply(s) for s in script]
            chat.get_stats.return_value = {}
            sdk.return_value = chat
            agent = _ScriptedAgent(silent_mode=True, skip_lemonade=True)
            agent.chat = chat
            agent.streaming = False
            return agent, chat

    return build


class TestMissingRequestedOutput:
    """The request named a file; the turn must not end without it."""

    def test_agent_is_reprompted_and_writes_the_file(self, scripted, tmp_path):
        agent, chat = scripted(
            [
                # First pass: the right answer, in prose, no file.
                _answer("South"),
                # After the nudge, it writes it.
                _call("write_file", path="answer.txt", content="South"),
                _answer("Written to answer.txt."),
            ]
        )
        agent.process_query(
            "Work out which region has the highest total sales, and write "
            "just that region name to answer.txt."
        )
        produced = tmp_path / "answer.txt"
        assert produced.is_file(), "guard never got the file written"
        assert produced.read_text(encoding="utf-8").strip() == "South"

    def test_guard_nudges_at_most_once(self, scripted, tmp_path):
        # A model that ignores the nudge must not spin: the turn ends.
        agent, chat = scripted([_answer("South"), _answer("South, really.")])
        result = agent.process_query(
            "Write just that region name to answer.txt.",
        )
        assert not (tmp_path / "answer.txt").exists()
        assert chat.send_messages.call_count == 2, "nudged more than once"
        assert result["result"]

    def test_no_nudge_when_the_file_is_already_there(self, scripted, tmp_path):
        (tmp_path / "answer.txt").write_text("South", encoding="utf-8")
        agent, chat = scripted([_answer("South")])
        agent.process_query("Write just that region name to answer.txt.")
        assert chat.send_messages.call_count == 1

    def test_no_nudge_when_the_request_names_no_file(self, scripted):
        agent, chat = scripted([_answer("Python 3.11.")])
        agent.process_query("Which Python version does this project require?")
        assert chat.send_messages.call_count == 1


class TestIdleTurn:
    """A turn that only *describes* work gets one chance to do it."""

    def test_stated_intent_with_no_tool_call_is_reprompted(self, scripted, tmp_path):
        agent, chat = scripted(
            [
                _answer("I'll now read the CSV and compute the mean."),
                _call("write_file", path="out.md", content="400"),
                _answer("The mean is 400."),
            ]
        )
        agent.process_query("Compute the mean duration.")
        assert chat.send_messages.call_count == 3
        assert (tmp_path / "out.md").is_file()

    def test_a_completed_answer_ends_the_turn(self, scripted):
        agent, chat = scripted([_answer("The mean is 400 seconds.")])
        agent.process_query("Compute the mean duration.")
        assert chat.send_messages.call_count == 1


class TestRepeatedCallLoop:
    """Repeating a call is a stall to break out of, not a task to conclude."""

    def test_the_agent_gets_one_chance_to_change_approach(self, scripted, tmp_path):
        agent, chat = scripted(
            [
                # Three identical reads teach it nothing...
                _call("read_file", path="src.txt"),
                _call("read_file", path="src.txt"),
                _call("read_file", path="src.txt"),
                # ...and after the nudge it does the actual work.
                _call("write_file", path="answer.txt", content="South"),
                _answer("Wrote answer.txt."),
            ]
        )
        (tmp_path / "src.txt").write_text("South", encoding="utf-8")
        agent.max_consecutive_repeats = 3
        agent.process_query("Read src.txt and write the region to answer.txt.")
        assert (tmp_path / "answer.txt").is_file(), "the nudge recovered nothing"

    def test_a_model_that_keeps_repeating_ends_without_claiming_success(
        self, scripted, tmp_path
    ):
        same = _call("read_file", path="src.txt")
        agent, chat = scripted([same] * 9)
        (tmp_path / "src.txt").write_text("South", encoding="utf-8")
        agent.max_consecutive_repeats = 3
        result = agent.process_query("Read src.txt and answer.")
        assert "Task completed" not in result["result"]
        assert "not finished" in result["result"]


class TestWorkspaceIsCwd:
    """The guard resolves names against the process cwd, as the loop does."""

    def test_file_written_into_a_subdirectory_does_not_satisfy_the_request(
        self, scripted, tmp_path
    ):
        os.makedirs(tmp_path / "sub", exist_ok=True)
        agent, chat = scripted(
            [
                _call("write_file", path="sub/answer.txt", content="South"),
                _answer("Wrote it."),
                _call("write_file", path="answer.txt", content="South"),
                _answer("Wrote answer.txt."),
            ]
        )
        agent.process_query("Write the region name to answer.txt.")
        assert (tmp_path / "answer.txt").is_file()
