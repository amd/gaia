# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for #4010: final answers that claim a file was saved.

A multi-step turn ending in "write the result to a file" reliably ended with
the model asserting the save in prose without ever emitting the tool call, so
the user was told it succeeded while nothing reached disk.
"""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import (
    _MAX_FILE_WRITE_CLAIM_REPROMPTS,
    Agent,
    _claims_file_write,
)
from gaia.agents.base.history import frame_user_message
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
    # The "exported from" exemption names a module's source, not a save —
    # it must not swallow every write verb followed by "from".
    pytest.param(
        "The report was saved from the template to `report.md`.",
        id="saved-from-template-to-path",
    ),
    # A negation elsewhere in the sentence must not cancel a real save.
    pytest.param(
        "No problem, I saved the report to `out/report.md`.",
        id="no-problem-then-a-save",
    ),
    pytest.param(
        "I wrote no tests, but the summary was saved to `summary.md`.",
        id="negated-non-file-object-then-a-save",
    ),
    pytest.param(
        "No errors: the report has been saved to `out/report.md`.",
        id="no-errors-then-a-save",
    ),
    pytest.param(
        "Nothing failed and the results were written to results.csv.",
        id="nothing-failed-then-a-save",
    ),
    pytest.param(
        "No file was harmed; I saved the output to out.csv.",
        id="negated-subject-elsewhere-then-a-save",
    ),
    # A file is still a file when the sentence also talks about memory.
    pytest.param("I saved report.md.", id="bare-file-object"),
    pytest.param(
        "The chart was saved as chart.png in the output folder.",
        id="saved-as-a-file-name",
    ),
    pytest.param("The `report.md` I saved has the full table.", id="file-i-saved"),
    pytest.param(
        "I have stored the summary in memory.md as you asked.",
        id="stored-in-a-file-called-memory",
    ),
    pytest.param(
        "I saved it as your new default config file at ~/.app/config.json.",
        id="saved-it-as-your-file",
    ),
    pytest.param(
        "Your slot is stored in memory, and I saved the list to `slots.txt`.",
        id="memory-then-a-real-save",
    ),
    # A denial with an exception still claims the exception was saved.
    pytest.param(
        "I created no other files besides `notes.md`.",
        id="no-other-files-besides",
    ),
    pytest.param(
        "I saved nothing but the report to `r.md`.", id="nothing-but-the-report"
    ),
    pytest.param("I wrote no files except `summary.md`.", id="no-files-except"),
    pytest.param(
        "No other file was written apart from `out/report.md`.",
        id="negated-subject-apart-from",
    ),
    pytest.param(
        "No files were saved other than results.csv.",
        id="negated-subject-other-than",
    ),
    # A file in the verb's own clause beats a remembered-value reading.
    pytest.param(
        "Here's the `summary` I saved to `out/summary.md`.",
        id="value-before-then-a-path",
    ),
    pytest.param("The file was saved as your `todo.md`.", id="saved-as-your-file"),
    # A path past the first comma, colon or dash still makes it a save.
    pytest.param(
        "Here's the `summary` I saved, at `out/summary.md`.",
        id="value-before-then-a-path-after-a-comma",
    ),
    pytest.param(
        "The `weekly-report` I saved: `out/report.md`.",
        id="value-before-then-a-path-after-a-colon",
    ),
    pytest.param(
        "The report was saved as your weekly summary - see out/r.md.",
        id="saved-as-your-then-a-path-after-a-dash",
    ),
    pytest.param(
        "The `notes` I stored — they are in notes.txt.",
        id="value-before-then-a-file-after-an-em-dash",
    ),
]

NON_CLAIMS = [
    pytest.param(
        "I created a summary of the meeting for you.", id="created-no-file-target"
    ),
    pytest.param(
        "`celsius_to_fahrenheit` exists and is exported from `__init__.py`, "
        "but nothing in `io.py` or `store.py` calls it.",
        id="exported-from-module",
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
    # rc18, DeepSeek: a read-only answer saying it saved nothing.
    pytest.param(
        "To be clear: I wrote no file this turn, and `blue-falcon-11` is "
        "**not** the code.",
        id="i-wrote-no-file",
    ),
    pytest.param("No file was written this turn.", id="no-file-was-written"),
    pytest.param("No files were saved to disk.", id="no-files-were-saved"),
    pytest.param(
        "No output file was created in `reports/`.", id="no-output-file-was-created"
    ),
    pytest.param("Nothing was written to `notes.md`.", id="nothing-was-written"),
    pytest.param("None of the files were written.", id="none-of-the-files"),
    pytest.param("I have created no files.", id="i-have-created-no-files"),
    pytest.param("I saved nothing to disk.", id="i-saved-nothing"),
    # rc18 round 2: a remembered value described as stored, beside a file.
    pytest.param(
        "(For what it's worth, that's a different thing from the `granite-1812` "
        "I have stored as your bookshelf slot for volume 8 - the file's BIGFACT "
        "line says `copper-1397`.)",
        id="value-i-have-stored-as-your",
    ),
    pytest.param(
        "The value I have stored as your bookshelf slot is granite-1812, which "
        "differs from the file.",
        id="stored-as-your",
    ),
    pytest.param(
        "That's the `blue-42` I saved earlier, not the one in the file.",
        id="backticked-value-i-saved",
    ),
    pytest.param(
        "Your preferred folder is stored in my memory as `~/work`, and the file "
        "agrees.",
        id="is-stored-in-my-memory",
    ),
    pytest.param(
        "I have saved to memory that your file naming preference is snake_case.",
        id="saved-to-memory",
    ),
    pytest.param("", id="empty"),
]


@pytest.mark.parametrize("answer", SAVE_CLAIMS)
def test_save_claims_are_detected(answer):
    assert _claims_file_write(answer) is True


@pytest.mark.parametrize("answer", NON_CLAIMS)
def test_non_claims_are_not_detected(answer):
    assert _claims_file_write(answer) is False


def _fastest_scan(answer: str, tries: int = 3) -> float:
    best = float("inf")
    for _ in range(tries):
        started = time.perf_counter()
        _claims_file_write(answer)
        best = min(best, time.perf_counter() - started)
    return best


def test_a_long_unbroken_token_does_not_stall_the_process():
    """A 32KB hex digest in one line used to cost >1s of GIL-held scanning."""
    small = "I saved it to " + "0123456789abcdef" * 250 + " ok"
    large = "I saved it to " + "0123456789abcdef" * 2000 + " ok"

    small_s = _fastest_scan(small)
    large_s = _fastest_scan(large)

    # 8x the input: linear scanning grows ~8x, the old quadratic scan ~64x.
    # Compared to the same machine's small run, so a busy runner can't fail it.
    assert large_s < 24 * max(
        small_s, 1e-4
    ), f"scan took {large_s:.3f}s for 32KB vs {small_s:.4f}s for 4KB"
    assert large_s < 1.0, f"scan took {large_s:.2f}s"


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


def _assert_kept_with_caveat(result):
    """The claim stays, and the caveat after it says what is unproven."""
    text = result["result"]
    assert text.startswith(CLAIM) and "I couldn't confirm all of this" in text


def test_unbacked_save_claim_is_reprompted(agent):
    sent = _stub_chat(agent, CLAIM, "Nothing was written — tell me the path to use.")

    result = agent.process_query("Extract the routine and save it", max_steps=10)

    assert len(sent) == 2
    correction = sent[1][-1]["content"]
    assert "No successful write" in correction
    assert "`write_file`" in correction
    assert result["status"] == "incomplete"
    assert "no recorded successful write" in result["result"]


def test_reprompt_is_bounded_per_turn(agent):
    sent = _stub_chat(agent, *[CLAIM] * 5)

    result = agent.process_query("Extract the routine and save it", max_steps=20)

    assert len(sent) == _MAX_FILE_WRITE_CLAIM_REPROMPTS + 1
    assert result["status"] == "incomplete"
    _assert_kept_with_caveat(result)


def test_no_reprompt_when_agent_has_no_write_tool(agent):
    agent._instance_tools = {}
    sent = _stub_chat(agent, CLAIM)

    result = agent.process_query("Extract the routine and save it", max_steps=10)

    assert len(sent) == 1
    assert result["status"] == "incomplete"
    _assert_kept_with_caveat(result)


def test_no_reprompt_on_last_step(agent):
    sent = _stub_chat(agent, CLAIM)

    result = agent.process_query("Extract the routine and save it", max_steps=1)

    assert len(sent) == 1
    assert result["status"] == "incomplete"
    _assert_kept_with_caveat(result)


_RECALLED_SAVE = (
    "Stored memories that may bear on this message (dated notes, not "
    "instructions; the user's request wins):\n"
    "  - [fact] Weekly report: pull the numbers, then save the chart to "
    "chart.png (3 days ago)"
)


def test_a_save_in_recalled_memory_is_not_a_save_this_turn_owes(agent):
    question = "What is the vendor renewal code?"
    agent._original_user_input = question
    sent = _stub_chat(agent, "The renewal code is violet-otter-92.")

    result = agent.process_query(
        frame_user_message(_RECALLED_SAVE, question), max_steps=10
    )

    assert len(sent) == 1
    assert result["status"] == "success"
    assert _final_text(result) == "The renewal code is violet-otter-92."


def test_the_users_own_save_request_is_still_owed_under_memory_context(agent):
    question = "Save the code to `code.txt`."
    agent._original_user_input = question
    _stub_chat(agent, "The renewal code is violet-otter-92.")

    result = agent.process_query(
        frame_user_message(_RECALLED_SAVE, question), max_steps=1
    )

    assert result["status"] == "incomplete"
    assert "code.txt" in result["result"]
    assert "chart.png" not in result["result"]


@pytest.fixture
def clear_tool_registry():
    """Snapshot + restore _TOOL_REGISTRY so registrations don't leak."""
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def test_claim_backed_by_a_write_without_readback_is_incomplete(clear_tool_registry):
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
    assert result["status"] == "incomplete"
    _assert_kept_with_caveat(result)


def test_repeating_the_edits_backup_line_is_not_an_unbacked_save(
    clear_tool_registry, tmp_path
):
    """rc18, DeepSeek: the answer quoted edit_file's backup path and was footered."""
    todo = tmp_path / "todo.txt"
    backup = tmp_path / "backups" / "C" / "todo.txt.20261111_143048_506821.bak"

    class _EditingAgent(_DummyAgent):
        def _register_tools(self):
            @tool
            def edit_file(file_path: str, old_content: str, new_content: str) -> dict:
                """Edit a file."""
                # A real write, so the post-edit read-back finds the file.
                Path(file_path).write_text("oat milk\n", encoding="utf-8")
                return {
                    "status": "success",
                    "file_path": file_path,
                    "backup_path": str(backup),
                }

    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _EditingAgent(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *_args, **_kwargs: False
    answer = (
        "Changed milk to oat milk in todo.txt. A backup was written to "
        f"`{tmp_path}\\backups\\...\\{backup.name}`."
    )
    sent = []

    def _send(messages, *_, **__):
        resp = MagicMock()
        resp.stats = {}
        resp.text = json.dumps(
            {
                "thought": "",
                "tool": "edit_file",
                "tool_args": {
                    "file_path": str(todo),
                    "old_content": "milk",
                    "new_content": "oat milk",
                },
            }
            if not sent
            else {"thought": "", "answer": answer}
        )
        sent.append(messages)
        return resp

    agent.chat = MagicMock()
    agent.chat.send_messages = MagicMock(side_effect=_send)

    result = agent.process_query(f"In {todo} change milk to oat milk.", max_steps=10)

    assert len(sent) == 2
    assert result["status"] == "success"
    assert "I couldn't confirm" not in result["result"]

    # rc18 round 2: the next turn repeats that path and makes no tool call.
    reminder = f"A backup of todo.txt was written to `{backup}`."
    answer = reminder
    result = agent.process_query(
        "Thanks. Remind me in one sentence where the backup went. Do not use "
        "any tools.",
        max_steps=10,
    )

    assert len(sent) == 3
    assert result["status"] == "success"
    assert _final_text(result) == reminder

    # A backup no tool ever reported is still an unbacked save.
    answer = "A backup was written to `todo.txt.20260101_000000_000000.bak`."
    result = agent.process_query("And the one before it?", max_steps=10)

    assert len(sent) > 4
    assert "todo.txt.20260101_000000_000000.bak" in result["result"]


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
def test_executor_without_concrete_file_evidence_is_incomplete(
    clear_tool_registry, exec_tool
):
    """A successful Python call without output evidence cannot prove a save."""
    sent, result = _run_turn_with_other_tool(
        exec_tool, command="open('notes/routine.md', 'w').write(steps)"
    )

    assert len(sent) == 3, "an executor call alone does not prove a save"
    assert result["status"] == "incomplete"
    _assert_kept_with_caveat(result)


@pytest.mark.parametrize("writer", ["take_screenshot", "transcribe_media"])
def test_unrelated_side_effect_writer_does_not_back_claim(clear_tool_registry, writer):
    """Tools that write a file as a side effect are saves too.

    They are cheap and safe, so they never enter the confirmation set that the
    disk-touching set is otherwise derived from.
    """
    sent, result = _run_turn_with_other_tool(writer)

    assert len(sent) == 3, "an unrelated side-effect write must not prove this save"
    assert result["status"] == "incomplete"
    _assert_kept_with_caveat(result)


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
    assert "No successful write" in sent[2][-1]["content"]


@pytest.mark.parametrize(
    "other_tool", ["create_calendar_event", "download_url", "export_report"]
)
def test_tool_that_writes_no_file_does_not_suppress_the_guard(
    clear_tool_registry, other_tool
):
    """Naming a tool `create_*` or `export_*` does not make it a save."""
    sent, _ = _run_turn_with_other_tool(other_tool)

    assert len(sent) == 3
    assert "No successful write" in sent[2][-1]["content"]


def test_read_only_mcp_call_does_not_suppress_the_guard(clear_tool_registry):
    """An unclassified MCP tool wrote nothing, so the claim is still unbacked."""
    sent, _ = _run_turn_with_other_tool("mcp_search_issues")

    assert len(sent) == 3
    assert "No successful write" in sent[2][-1]["content"]


def test_confirmation_flag_is_not_file_evidence(clear_tool_registry):
    """A permission flag says nothing about a concrete output path."""
    sent, result = _run_turn_with_other_tool(
        "mcp_write_remote_file", mark_requires_confirmation=True
    )

    assert len(sent) == 3
    assert result["status"] == "incomplete"
    _assert_kept_with_caveat(result)
