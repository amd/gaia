# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A turn that wrote a file from other files is asked once to compare them.

"Built and verified" without opening the output is how dropped rows and
unreconciled totals reach the user. Before such a turn's answer goes out the
model gets one reminder to re-open what it wrote and reconcile it with what it
read. It goes out at most once a turn, and never on a turn with nothing to
reconcile: a chat answer, a code edit, or a file written from nothing.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.completion import (
    RECONCILE_TAG,
    CompletionEvidence,
    reconcile_check,
)
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator

# ---------------------------------------------------------------------------
# What counts as an input
# ---------------------------------------------------------------------------


def _evidence(tmp_path):
    return CompletionEvidence("Build out.csv", str(tmp_path))


def _ok(evidence, tool, **args):
    evidence.record(tool, args, {"status": "success", **args}, True)


def test_a_file_read_before_the_write_is_an_input(tmp_path):
    (tmp_path / "in.csv").write_text("a\n1\n")
    (tmp_path / "out.csv").write_text("a\n1\n")
    evidence = _evidence(tmp_path)
    _ok(evidence, "read_file", file_path=str(tmp_path / "in.csv"))
    _ok(evidence, "write_file", file_path=str(tmp_path / "out.csv"))
    _ok(evidence, "read_file", file_path=str(tmp_path / "out.csv"))

    written = evidence.written_files()
    assert [Path(p).name for p in written] == ["out.csv"]
    assert [Path(p).name for p in evidence.inputs_for(written)] == ["in.csv"]


def test_reading_back_the_output_is_not_an_input(tmp_path):
    (tmp_path / "out.csv").write_text("a\n1\n")
    evidence = _evidence(tmp_path)
    _ok(evidence, "write_file", file_path=str(tmp_path / "out.csv"))
    _ok(evidence, "read_file", file_path=str(tmp_path / "out.csv"))
    assert evidence.inputs_for(evidence.written_files()) == []


def test_the_reminder_names_the_files_and_the_three_steps():
    text = reconcile_check(["out.csv"], ["in.csv", "ledger.xlsx"])
    assert text.startswith(RECONCILE_TAG)
    assert "`out.csv`" in text and "`in.csv`, `ledger.xlsx`" in text
    for step in ("Open each file you wrote", "rows or items in", "Fix what does not"):
        assert step in text
    assert "do not repeat it" in text


def test_a_long_list_is_cut():
    text = reconcile_check([f"out{i}.csv" for i in range(8)], ["in.csv"])
    assert "`out4.csv` and 3 more" in text


# ---------------------------------------------------------------------------
# Through the real loop
# ---------------------------------------------------------------------------


class _FileAgent(Agent, FileIOToolsMixin):
    def _get_system_prompt(self):
        return "Use tools for files."

    def _register_tools(self):
        self.path_validator = PathValidator(allowed_paths=[str(Path.cwd())])
        self.register_file_io_tools()


@pytest.fixture
def agent(tmp_path, monkeypatch):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        instance = _FileAgent(silent_mode=True, skip_lemonade=True)
    instance.streaming = False
    instance._tool_requires_confirmation = lambda *a, **kw: False
    instance.console = MagicMock()
    instance.console.cancelled = None
    yield instance
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def _script(agent, *turns):
    """Scripted model replies; returns the message lists each call was sent."""
    left = list(turns)
    sent = []

    def send(messages, *_a, **_kw):
        sent.append([dict(m) for m in messages])
        assert left, "unexpected model call"
        return MagicMock(text=json.dumps(left.pop(0)), stats={})

    agent.chat.send_messages.side_effect = send
    return sent, left


def _call(tool_name, **args):
    return {"tool": tool_name, "tool_args": args}


def _reminders(messages):
    return [
        m
        for m in messages
        if m.get("role") == "user"
        and str(m.get("content", "")).startswith(RECONCILE_TAG)
    ]


REQUEST = "Total the amounts in sales.csv and save the result as totals.csv"
ANSWER = "I totalled sales.csv into totals.csv: 2 rows in, total 30."


def _sales(tmp_path):
    (tmp_path / "sales.csv").write_text("item,amount\na,10\nb,20\n")


def test_the_model_is_asked_once_and_one_step_is_enough(agent, tmp_path):
    _sales(tmp_path)
    sent, left = _script(
        agent,
        _call("read_file", file_path="sales.csv"),
        _call("write_file", file_path="totals.csv", content="total\n30\n"),
        _call("read_file", file_path="totals.csv"),
        {"answer": ANSWER},
        {"answer": ANSWER},
    )

    result = agent.process_query(REQUEST, max_steps=12)

    assert not left
    # Four steps of work, then exactly one more for the check.
    assert len(sent) == 5
    assert _reminders(sent[3]) == []
    asked = _reminders(sent[4])
    assert len(asked) == 1
    assert "totals.csv" in asked[0]["content"] and "sales.csv" in asked[0]["content"]
    assert result["result"].startswith(ANSWER)
    assert RECONCILE_TAG not in result["result"]
    assert result["completion_gaps"] == []


def test_work_done_after_the_reminder_does_not_bring_another(agent, tmp_path):
    _sales(tmp_path)
    fixed = "I totalled sales.csv into totals.csv: 2 rows in, total 30 (corrected)."
    sent, left = _script(
        agent,
        _call("read_file", file_path="sales.csv"),
        _call("write_file", file_path="totals.csv", content="total\n3\n"),
        _call("read_file", file_path="totals.csv"),
        {"answer": ANSWER},
        _call("write_file", file_path="totals.csv", content="total\n30\n"),
        _call("read_file", file_path="totals.csv"),
        {"answer": fixed},
    )

    result = agent.process_query(REQUEST, max_steps=12)

    assert not left
    assert len(_reminders(sent[-1])) == 1
    assert (tmp_path / "totals.csv").read_text() == "total\n30\n"
    assert result["result"].startswith(fixed)


def test_a_question_about_a_file_gets_no_reminder(agent, tmp_path):
    _sales(tmp_path)
    sent, left = _script(
        agent,
        _call("read_file", file_path="sales.csv"),
        {"answer": "The amounts in sales.csv add up to 30."},
    )

    agent.process_query("What do the amounts in sales.csv add up to?", max_steps=8)

    assert not left
    assert len(sent) == 2
    assert not any(_reminders(messages) for messages in sent)


def test_a_file_written_from_nothing_gets_no_reminder(agent, tmp_path):
    sent, left = _script(
        agent,
        _call("write_file", file_path="haiku.md", content="An old silent pond\n"),
        _call("read_file", file_path="haiku.md"),
        {"answer": "I wrote the haiku to haiku.md."},
    )

    agent.process_query("Write a haiku and save it as haiku.md", max_steps=8)

    assert not left
    assert not any(_reminders(messages) for messages in sent)


def test_a_code_file_gets_no_reminder(agent, tmp_path):
    _sales(tmp_path)
    sent, left = _script(
        agent,
        _call("read_file", file_path="sales.csv"),
        _call("write_file", file_path="total.py", content="print(30)\n"),
        _call("read_file", file_path="total.py"),
        {"answer": "I wrote total.py, which prints the total of sales.csv."},
    )

    agent.process_query(
        "Write total.py that prints the total of sales.csv", max_steps=8
    )

    assert not left
    assert not any(_reminders(messages) for messages in sent)
