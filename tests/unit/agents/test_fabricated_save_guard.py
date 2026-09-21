# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for #4010: final answers that claim a file was saved.

A multi-step turn ending in "write the result to a file" reliably ended with
the model asserting the save in prose without ever emitting the tool call, so
the user was told it succeeded while nothing reached disk.
"""

import json
import time
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
    # #4057: phrasings taken verbatim from live Gemma-4-E4B runs that the
    # first cut of the detector let through.
    pytest.param(
        "The report has been successfully written and saved to `out/summary.md`.",
        id="adverb-between-been-and-verb",
    ),
    pytest.param(
        "Report saved successfully at `out/report.md`.",
        id="subject-then-bare-participle",
    ),
    pytest.param(
        "Created the file `out/hello.txt` containing the text HELLO.",
        id="created-the-file-object",
    ),
    pytest.param(
        "The script successfully wrote the file `out/generated.txt`.",
        id="third-person-wrote-the-file",
    ),
    # A plan label in front of a completed save is still a completed save —
    # otherwise skipping plan prose is a one-token bypass of the guard.
    pytest.param(
        "Step 3: I saved the report to out/summary.md.",
        id="plan-label-in-front-of-a-real-claim",
    ),
    pytest.param(
        "**Plan:** the file was written to out/x.md.",
        id="plan-label-in-front-of-a-passive-claim",
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
    # #4057: plan steps name a save the model still intends to make. The guard
    # gets one re-prompt per turn, so spending it here lets a real fabrication
    # later in the same turn through unblocked.
    pytest.param(
        "**Completion:** Conclude by stating the precise path where the summary "
        "was saved, as requested.",
        id="plan-step-naming-a-future-save",
    ),
    pytest.param(
        "**Step 3:** Confirm to the user the exact path where the file was written.",
        id="numbered-plan-step",
    ),
    # A negation is the opposite of a claim, so the one-word subject slot must
    # not read one as one.
    pytest.param("Nothing saved to disk.", id="nothing-saved"),
    pytest.param("Not saved to out.md.", id="not-saved"),
    pytest.param("Never written to the file.", id="never-written"),
    pytest.param("", id="empty"),
]


@pytest.mark.parametrize("answer", SAVE_CLAIMS)
def test_save_claims_are_detected(answer):
    assert _claims_file_write(answer) is True


@pytest.mark.parametrize("answer", NON_CLAIMS)
def test_non_claims_are_not_detected(answer):
    assert _claims_file_write(answer) is False


def test_a_long_unbroken_token_does_not_stall_the_process():
    """A 32KB hex digest in one line used to cost >1s of GIL-held scanning."""
    answer = "I saved it to " + "0123456789abcdef" * 2000 + " ok"

    started = time.perf_counter()
    _claims_file_write(answer)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.2, f"scan took {elapsed:.2f}s"


def test_a_path_longer_than_the_scan_bound_is_still_detected():
    deep = "/".join(["segment"] * 40) + "/routine.md"

    assert len(deep) > 80
    assert _claims_file_write(f"I saved it to {deep}") is True


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


def _run_turn_with_other_tool(
    tool_name,
    mark_requires_confirmation=False,
    command="save the routine",
    result=None,
):
    """Run a turn where `tool_name` is called and the answer claims a save.

    Returns the messages sent to the LLM — two batches mean the claim was
    accepted, three mean the guard re-prompted.
    """
    calls = []

    class _OtherToolAgent(_DummyAgent):
        def _register_tools(self):
            @tool
            def write_file(file_path: str, content: str) -> dict:
                """Write content to a file."""
                return {"status": "success", "file_path": file_path}

            def _other(command: str) -> dict:
                calls.append(command)
                return dict(result) if result else {"status": "success", "stdout": ""}

            _other.__name__ = tool_name
            _other.__doc__ = "Run a command."
            tool(_other)

    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _OtherToolAgent(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *_args, **_kwargs: False
    if mark_requires_confirmation:
        agent._tools_registry[tool_name]["requires_confirmation"] = True

    sent = []

    def _send(messages, *_, **__):
        resp = MagicMock()
        resp.stats = {}
        if not sent:
            resp.text = json.dumps(
                {
                    "thought": "working",
                    "tool": tool_name,
                    "tool_args": {"command": command},
                }
            )
        else:
            resp.text = json.dumps({"thought": "", "answer": CLAIM})
        sent.append([dict(m) for m in messages])
        return resp

    agent.chat = MagicMock()
    agent.chat.send_messages = MagicMock(side_effect=_send)

    outcome = agent.process_query("Extract the routine and save it", max_steps=10)

    assert calls == [command]
    return sent, outcome


# run_shell_command is in the disk-touching set but is deliberately absent
# here: its read-only allowlist and blocked redirection operators mean the real
# tool cannot perform a save, so a test asserting one would be fiction.
@pytest.mark.parametrize("exec_tool", ["run_python", "execute_python_file"])
def test_claim_backed_by_an_exec_tool_call_is_accepted(clear_tool_registry, exec_tool):
    """A save done by running Python is a real save."""
    sent, result = _run_turn_with_other_tool(
        exec_tool, command="open('notes/routine.md', 'w').write(steps)"
    )

    assert len(sent) == 2, "the guard re-prompted a save that the exec tool performed"
    assert _final_text(result) == CLAIM


@pytest.mark.parametrize("writer", ["take_screenshot", "transcribe_media"])
def test_claim_backed_by_a_side_effect_writer_is_accepted(clear_tool_registry, writer):
    """Tools that write a file as a side effect are saves too.

    They are cheap and safe, so they never enter the confirmation set that the
    disk-touching set is otherwise derived from.
    """
    sent, result = _run_turn_with_other_tool(writer)

    assert len(sent) == 2, "the guard re-prompted a save that the tool performed"
    assert _final_text(result) == CLAIM


@pytest.mark.parametrize(
    "outcome",
    [
        pytest.param({"status": "error", "error": "path not allowed"}, id="error"),
        pytest.param({"status": "denied", "error": "user declined"}, id="denied"),
    ],
)
def test_write_that_did_not_succeed_does_not_suppress_the_guard(
    clear_tool_registry, outcome
):
    """A refused or declined write left nothing on disk — #4010's exact harm.

    The call was logged before it ran, so counting attempts would silence the
    guard on the failure it exists to catch.
    """
    sent, _ = _run_turn_with_other_tool(
        "write_python_file", command="notes/routine.md", result=outcome
    )

    assert len(sent) == 3
    assert "no file-writing tool ran in this turn" in sent[2][-1]["content"]


@pytest.mark.parametrize(
    "other_tool", ["create_calendar_event", "download_url", "export_report"]
)
def test_tool_that_writes_no_file_does_not_suppress_the_guard(
    clear_tool_registry, other_tool
):
    """Naming a tool `create_*` or `export_*` does not make it a save."""
    sent, _ = _run_turn_with_other_tool(other_tool)

    assert len(sent) == 3
    assert "no file-writing tool ran in this turn" in sent[2][-1]["content"]


def test_read_only_mcp_call_does_not_suppress_the_guard(clear_tool_registry):
    """An unclassified MCP tool wrote nothing, so the claim is still unbacked."""
    sent, _ = _run_turn_with_other_tool("mcp_search_issues")

    assert len(sent) == 3
    assert "no file-writing tool ran in this turn" in sent[2][-1]["content"]


def test_mcp_tool_that_declares_confirmation_is_believed(clear_tool_registry):
    """A third-party tool that flags itself consequential can have written."""
    sent, result = _run_turn_with_other_tool(
        "mcp_write_remote_file", mark_requires_confirmation=True
    )

    assert len(sent) == 2
    assert _final_text(result) == CLAIM
