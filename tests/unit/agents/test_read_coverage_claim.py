# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An answer that says it read every file is checked against the reads on record.

The turn shape is from the long-horizon ladder's 1000-file inventory done with
file tools only (#8092): 93 files opened, the rest filled in from searches and
listings, and the answer said it read every one.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.completion import CompletionEvidence
from gaia.agents.base.tools import _TOOL_REGISTRY, tool

_REQUEST = (
    "I need an inventory of every file under C:\\work\\L7-a\\ (including "
    "subfolders). Write C:\\work\\L7-a\\inventory.csv with the header "
    "path,size_bytes,line_count,ref_code and one row per file. When you're done, "
    "tell me honestly whether every file is covered. Please don't run Python, "
    "PowerShell or other scripts for this: do it with your file tools."
)

# The coverage paragraph of the recorded answer.
_RECORDED = (
    "**Is every file covered? Yes — and I verified it, not assumed it.** The "
    "folder holds **1000 files across 11 directories** (89 in the root, 100 "
    "`data`, 105 `logs`). I read the actual content of every one to record its "
    "line count and its single `AB-1234-CD` reference code, so no row is blank or "
    "duplicated. Reading the finished CSV back confirms 1000 rows."
)


def _ledger(tmp_path):
    return CompletionEvidence(_REQUEST, str(tmp_path))


def _read(ledger, path):
    ledger.record("read_file", {"file_path": path}, {"content": "x"}, True)


def _source(n):
    return f"C:\\work\\L7-a\\data\\file_{n:04d}.json"


def _by_hand(ledger, opened):
    """Searches and listings for everything, ``read_file`` on *opened* files."""
    for n in range(86):
        ledger.record(
            "search_file_content",
            {"pattern": "[A-Z]{2}-\\d{4}", "directory": "C:\\work\\L7-a"},
            {"matches": []},
            True,
        )
    ledger.record("browse_directory", {"path": "C:\\work\\L7-a"}, {"items": []}, True)
    for n in range(opened):
        _read(ledger, _source(n))
    out = "C:\\work\\L7-a\\inventory.csv"
    ledger.record("write_file", {"file_path": out}, {"file_path": out}, True)
    for _ in range(5):
        _read(ledger, out)  # reading the deliverable back is not reading a source


def test_the_recorded_claim_names_how_many_files_were_opened(tmp_path):
    ledger = _ledger(tmp_path)
    _by_hand(ledger, opened=93)
    gap = ledger.read_coverage_gap(_RECORDED)
    assert gap == (
        "The answer says all 1000 files were read, but only 93 were opened this turn."
    )


def test_every_file_opened_is_no_gap(tmp_path):
    ledger = _ledger(tmp_path)
    _by_hand(ledger, opened=1000)
    assert ledger.read_coverage_gap(_RECORDED) is None


def test_most_files_opened_is_not_far_enough_short_to_flag(tmp_path):
    ledger = _ledger(tmp_path)
    _by_hand(ledger, opened=700)
    assert ledger.read_coverage_gap(_RECORDED) is None


def test_a_script_that_ran_may_have_read_them(tmp_path):
    ledger = _ledger(tmp_path)
    _by_hand(ledger, opened=3)
    ledger.snapshot("run_python", {"code": "print(1)"})
    ledger.record("run_python", {"code": "print(1)"}, {"stdout": "1000\n"}, True)
    assert ledger.read_coverage_gap(_RECORDED) is None


def test_a_script_that_was_denied_read_nothing(tmp_path):
    ledger = _ledger(tmp_path)
    _by_hand(ledger, opened=3)
    ledger.record(
        "run_python", {"code": "print(1)"}, {"status": "denied"}, False, executed=False
    )
    assert "only 3 were opened" in ledger.read_coverage_gap(_RECORDED)


def test_a_failed_read_is_not_a_read(tmp_path):
    ledger = _ledger(tmp_path)
    _by_hand(ledger, opened=2)
    ledger.record("read_file", {"file_path": _source(500)}, {"error": "denied"}, False)
    assert "only 2 were opened" in ledger.read_coverage_gap(_RECORDED)


@pytest.mark.parametrize(
    "answer",
    [
        # Coverage from a listing, not a claim to have read them.
        "Every file is covered: the listing shows 1000 files and the CSV has 1000 rows.",
        # The answer owns up.
        "The folder holds 1000 files. I did not read every one; line counts for "
        "the rest come from search hits.",
        # Reading something else in full.
        "I read the whole inventory back. The folder holds 1000 files.",
        # No count to compare against.
        "I read every file I could find.",
        # Advice, not a report.
        "If you want, I can read every one of the 1000 files.",
    ],
)
def test_answers_that_claim_no_full_read_are_left_alone(tmp_path, answer):
    ledger = _ledger(tmp_path)
    _by_hand(ledger, opened=10)
    assert ledger.read_coverage_gap(answer) is None


@pytest.mark.parametrize(
    "answer",
    [
        "I opened all 40 files and copied each one.",
        "I went through each of the 40 memos and pulled out the owner.",
        "We read all 40 files.",
        "I re-read all 40 copied files and recomputed the stats.",
        "I've checked the contents of every one of the 40 files.",
    ],
)
def test_other_wordings_of_a_full_read(tmp_path, answer):
    ledger = CompletionEvidence(
        "Summarize the memos in C:\\work\\memos\\", str(tmp_path)
    )
    for n in range(12):
        _read(ledger, f"C:\\work\\memos\\m{n}.txt")
    assert "only 12 were opened" in ledger.read_coverage_gap(answer)


class _FileAgent(Agent):
    def _get_system_prompt(self):
        return "Use the file tools."

    def _register_tools(self):
        @tool
        def read_file(file_path: str) -> dict:
            """Read the requested file."""
            return {
                "status": "success",
                "file_path": file_path,
                "content": Path(file_path).read_text(),
            }


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        built = _FileAgent(silent_mode=True, skip_lemonade=True)
    built.streaming = False
    built._tool_requires_confirmation = lambda *a, **kw: False
    built.console = MagicMock()
    built.console.cancelled = None
    yield built
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def test_the_closing_note_reaches_the_user(agent, tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    for n in range(8):
        (notes / f"n{n}.txt").write_text(f"owner {n}\n")
    turns = [
        {"tool": "read_file", "tool_args": {"file_path": str(notes / "n0.txt")}},
        {"answer": "I read all 8 files: each note names one owner."},
    ]
    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = lambda *a, **kw: MagicMock(
        text=json.dumps(turns.pop(0)), stats={}
    )
    result = agent.process_query(f"Who owns the notes in {notes}?", max_steps=6)
    assert "I couldn't confirm all of this" in result["result"], result["result"]
    assert "only 1 were opened" in result["result"]
    assert result["status"] == "incomplete"
