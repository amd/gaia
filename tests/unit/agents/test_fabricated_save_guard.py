# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for #4010: final answers that claim a file was saved.

A multi-step turn ending in "write the result to a file" reliably ended with
the model asserting the save in prose without ever emitting the tool call, so
the user was told it succeeded while nothing reached disk.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import (
    _MAX_FILE_WRITE_CLAIM_REPROMPTS,
    Agent,
    _claims_file_write,
)
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.base.verification import strip_verification_scope

SAVE_CLAIMS = [
    pytest.param(
        "I have saved the result to `notes/routine.md`.",
        id="i-have-saved-to-path",
    ),
    pytest.param(
        "I have saved the result to `X`.",
        id="i-have-saved-to-bare-backticked-name",
    ),
    pytest.param(
        "The routine has been saved to C:\\Users\\me\\routine.md.",
        id="has-been-saved-windows-path",
    ),
    pytest.param(
        "Done. The transcript was written to the file you asked for.",
        id="was-written-to-the-file",
    ),
    pytest.param(
        "Here is the summary.\n\nSaved to ~/Documents/summary.md",
        id="bare-saved-to",
    ),
    pytest.param(
        "I've now successfully created summary.txt with the extracted content.",
        id="ive-created-filename",
    ),
    pytest.param(
        "The extracted steps are stored in /tmp/steps.json for later use.",
        id="are-stored-in-path",
    ),
]

NON_CLAIMS = [
    pytest.param(
        "I created a summary of the meeting for you.", id="created-no-file-target"
    ),
    pytest.param(
        "I wrote a helper that normalizes the timestamps.", id="wrote-code-not-file"
    ),
    pytest.param(
        "To save it yourself, run:\n\n```bash\ngaia write_file out.md\n```",
        id="instruction-in-fence",
    ),
    pytest.param(
        "The file does not exist yet — tell me where you want it.",
        id="file-mentioned-no-claim",
    ),
    pytest.param(
        "Labeled #4 as `question` and #5 as `duplicate`. All triaged.",
        id="backticked-labels-not-paths",
    ),
    pytest.param(
        "I read `report.pdf` and summarized the three findings below.",
        id="read-not-written",
    ),
    pytest.param(
        "I will save the routine to routine.md once you confirm the path.",
        id="future-tense",
    ),
    pytest.param(
        "I've created the release notes for v0.17.5 and they look good.",
        id="version-number-not-a-path",
    ),
    pytest.param(
        "I have created the issue at https://github.com/amd/gaia/issues/42.",
        id="url-not-a-path",
    ),
    pytest.param(
        "I created a draft reply to john.doe@example.com.",
        id="email-address-not-a-path",
    ),
    pytest.param(
        "I have created an event at 10.30 in your calendar.",
        id="clock-time-not-a-path",
    ),
    pytest.param(
        "I wrote to john at acme.com.",
        id="domain-not-a-path",
    ),
    pytest.param("", id="empty"),
]


@pytest.mark.parametrize("answer", SAVE_CLAIMS)
def test_save_claims_are_detected(answer):
    assert _claims_file_write(answer) is True


@pytest.mark.parametrize("answer", NON_CLAIMS)
def test_non_claims_are_not_detected(answer):
    assert _claims_file_write(answer) is False


# ---------------------------------------------------------------------------
# Loop integration
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
        a._instance_tools = {"write_file": MagicMock()}
        return a


def _stub_chat(agent, *answers):
    responses = [json.dumps({"thought": "", "answer": a}) for a in answers]
    sent = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        resp = MagicMock()
        resp.text = responses.pop(0)
        resp.stats = {}
        return resp

    chat = MagicMock()
    chat.send_messages = MagicMock(side_effect=_send)
    agent.chat = chat
    return sent


def _final_text(result):
    return strip_verification_scope(result["result"]).strip()


CLAIM = "I have saved the routine to `notes/routine.md`."


def test_unbacked_save_claim_is_reprompted(agent):
    sent = _stub_chat(agent, CLAIM, "Nothing was written — tell me the path to use.")

    result = agent.process_query("Extract the routine and save it", max_steps=10)

    assert len(sent) == 2
    correction = sent[1][-1]["content"]
    assert "no file-writing tool ran in this turn" in correction
    assert "`write_file`" in correction
    assert _final_text(result) == "Nothing was written — tell me the path to use."


def test_reprompt_is_bounded_per_turn(agent):
    sent = _stub_chat(agent, *[CLAIM] * 5)

    result = agent.process_query("Extract the routine and save it", max_steps=20)

    assert len(sent) == _MAX_FILE_WRITE_CLAIM_REPROMPTS + 1
    assert _final_text(result) == CLAIM


def test_no_reprompt_when_agent_has_no_write_tool(agent):
    agent._instance_tools = {}
    sent = _stub_chat(agent, CLAIM)

    result = agent.process_query("Extract the routine and save it", max_steps=10)

    assert len(sent) == 1
    assert _final_text(result) == CLAIM


def test_no_reprompt_on_last_step(agent):
    sent = _stub_chat(agent, CLAIM)

    result = agent.process_query("Extract the routine and save it", max_steps=1)

    assert len(sent) == 1
    assert _final_text(result) == CLAIM


@pytest.fixture
def clear_tool_registry():
    """Snapshot + restore _TOOL_REGISTRY so registrations don't leak."""
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def test_claim_backed_by_a_write_tool_call_is_accepted(clear_tool_registry):
    writes = []

    class _WritingAgent(_DummyAgent):
        def _register_tools(self):
            @tool
            def write_file(file_path: str, content: str) -> dict:
                """Write content to a file."""
                writes.append(file_path)
                return {"status": "success", "file_path": file_path}

    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _WritingAgent(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *_args, **_kwargs: False

    sent = []

    def _send(messages, *_, **__):
        resp = MagicMock()
        resp.stats = {}
        if not sent:
            resp.text = json.dumps(
                {
                    "thought": "saving",
                    "tool": "write_file",
                    "tool_args": {"file_path": "notes/routine.md", "content": "steps"},
                }
            )
        else:
            resp.text = json.dumps({"thought": "", "answer": CLAIM})
        sent.append(messages)
        return resp

    agent.chat = MagicMock()
    agent.chat.send_messages = MagicMock(side_effect=_send)

    result = agent.process_query("Extract the routine and save it", max_steps=10)

    assert writes == ["notes/routine.md"]
    assert _final_text(result) == CLAIM


@pytest.mark.parametrize(
    "exec_tool", ["run_shell_command", "run_python", "execute_python_file"]
)
def test_claim_backed_by_an_exec_tool_call_is_accepted(clear_tool_registry, exec_tool):
    """A save done via the shell or a Python snippet is a real save."""
    calls = []

    class _ExecAgent(_DummyAgent):
        def _register_tools(self):
            @tool
            def write_file(file_path: str, content: str) -> dict:
                """Write content to a file."""
                return {"status": "success", "file_path": file_path}

            def _exec(command: str) -> dict:
                calls.append(command)
                return {"status": "success", "stdout": ""}

            _exec.__name__ = exec_tool
            _exec.__doc__ = "Run a command that may write files."
            tool(_exec)

    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _ExecAgent(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *_args, **_kwargs: False

    sent = []

    def _send(messages, *_, **__):
        resp = MagicMock()
        resp.stats = {}
        if not sent:
            resp.text = json.dumps(
                {
                    "thought": "saving via exec",
                    "tool": exec_tool,
                    "tool_args": {"command": "echo steps > notes/routine.md"},
                }
            )
        else:
            resp.text = json.dumps({"thought": "", "answer": CLAIM})
        sent.append(messages)
        return resp

    agent.chat = MagicMock()
    agent.chat.send_messages = MagicMock(side_effect=_send)

    result = agent.process_query("Extract the routine and save it", max_steps=10)

    assert calls == ["echo steps > notes/routine.md"]
    assert len(sent) == 2, "the guard re-prompted a save that the exec tool performed"
    assert _final_text(result) == CLAIM
