# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Notes the turn-end checks added to work that was in fact done.

Each was a true statement about the framework's own records and a false one
about the work: a file "never written" that a tool had saved, a file "not
read back" that a check tool had just opened, figures "with no source" that
the turn's script computed, and a check tool's findings counted as the tool
failing. A turn whose file exists ends "success"; what is left to say is a
caveat in the answer.
"""

import json
import logging
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.completion import (
    CompletionEvidence,
    blocking_gaps,
    caveated_answer,
)
from gaia.agents.base.deliverables import file_findings, text_blocks
from gaia.agents.base.tool_outcome import tool_result_failed
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.base.verification import observed_text, verification_record
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator

# ---------------------------------------------------------------------------
# A check that found problems is a call that worked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [
        {"status": "errors_found", "ok": False, "errors": [{"cell": "B2"}]},
        {"status": "no_formulas", "ok": False, "summary": "typed values only"},
        json.dumps({"status": "fail", "ok": False, "issues": ["slide 2 overflows"]}),
    ],
)
def test_findings_are_not_a_failed_call(result):
    assert tool_result_failed(result) is False


@pytest.mark.parametrize(
    "result",
    [
        {"ok": False, "error": "no such mailbox"},
        {"ok": False},
        {"status": "error", "ok": False, "error": "could not open the file"},
        {"status": "denied", "ok": False},
        {"status": "errors_found", "ok": False, "error": "check crashed"},
        {"status": "errors_found", "success": False},
    ],
)
def test_a_call_that_did_not_work_still_fails(result):
    assert tool_result_failed(result) is True


class _CheckAgent(Agent, FileIOToolsMixin):
    def _get_system_prompt(self):
        return "Use tools for files."

    def _register_tools(self):
        self.path_validator = PathValidator(allowed_paths=[str(Path.cwd())])
        self.register_file_io_tools()

        @tool
        def check_file(file_path: str) -> dict:
            """Check a file and report what is wrong in it."""
            return {
                "status": "errors_found",
                "ok": False,
                "file_path": file_path,
                "errors": [{"line": 1, "problem": "total does not add up"}],
            }

        @tool
        def export_file(output_path: str) -> dict:
            """Save a report at output_path."""
            # Past the file clock's slack, as any real export is.
            time.sleep(0.05)
            Path(output_path).write_text("report\n", encoding="utf-8")
            return {"status": "success", "output_path": output_path}


@pytest.fixture
def agent(tmp_path, monkeypatch):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GAIA_AGENT_SELF_CHECK", "0")
    with patch("gaia.agents.base.agent.AgentSDK"):
        instance = _CheckAgent(silent_mode=True, skip_lemonade=True)
    instance.streaming = False
    instance._tool_requires_confirmation = lambda *a, **kw: False
    instance.console = MagicMock()
    instance.console.cancelled = None
    yield instance
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def _script(agent, *turns):
    left = list(turns)

    def send(*_a, **_kw):
        assert left, "unexpected model call"
        return MagicMock(text=json.dumps(left.pop(0)), stats={})

    agent.chat.send_messages.side_effect = send
    return left


def _call(tool_name, **args):
    return {"tool": tool_name, "tool_args": args}


def test_a_check_tools_findings_are_not_a_tool_error(agent, tmp_path, caplog):
    (tmp_path / "sheet.csv").write_text("a,b\n1,2\n")
    left = _script(
        agent,
        _call("check_file", file_path=str(tmp_path / "sheet.csv")),
        {"answer": "sheet.csv has one problem: the total does not add up."},
    )

    with caplog.at_level(logging.WARNING, logger="gaia.agents.base.agent"):
        result = agent.process_query("Check sheet.csv", max_steps=6)

    assert not left
    assert result["error_count"] == 0
    assert result["status"] == "success"
    assert not [r for r in caplog.records if "Tool execution error" in r.getMessage()]
    agent.console.print_error.assert_not_called()


# ---------------------------------------------------------------------------
# A file a tool reports saving counts as written
# ---------------------------------------------------------------------------


def test_a_tool_that_reports_its_saved_path_wrote_the_file(agent, tmp_path):
    out = tmp_path / "report.pdf"
    left = _script(
        agent,
        _call("export_file", output_path=str(out)),
        {"answer": f"I saved the report to `{out}`."},
    )

    result = agent.process_query(f"Export the report to `{out}`", max_steps=6)

    assert not left
    assert out.exists()
    assert "No successful write" not in result["result"]
    assert result["completion_gaps"] == []
    assert result["status"] == "success", result["result"]


def test_a_path_a_tool_only_names_is_not_a_write(tmp_path):
    """A check tool echoes the file it read: that file did not change."""
    old = tmp_path / "old.txt"
    old.write_text("x")
    stale = time.time() - 60
    os.utime(old, (stale, stale))
    evidence = CompletionEvidence("Check old.txt", str(tmp_path))

    evidence.snapshot("check_file", {"file_path": str(old)})
    evidence.record(
        "check_file", {}, {"status": "success", "file_path": str(old)}, True
    )

    assert evidence.written_files() == []


def test_a_nested_or_relative_report_is_read_carefully(tmp_path):
    out = tmp_path / "list.csv"
    evidence = CompletionEvidence("Save the list to list.csv", str(tmp_path))

    evidence.snapshot("export-skill/save", {})
    time.sleep(0.05)
    out.write_text("a\n")
    evidence.record(
        "export-skill/save",
        {},
        {"status": "success", "output": {"written": True, "path": str(out)}},
        True,
    )
    assert [Path(p).name for p in evidence.written_files()] == ["list.csv"]

    # A bare name could be any folder's file: not taken as proof.
    other = CompletionEvidence("Save it", str(tmp_path))
    other.snapshot("export-skill/save", {})
    other.record(
        "export-skill/save", {}, {"status": "success", "path": "list.csv"}, True
    )
    assert other.written_files() == []


# ---------------------------------------------------------------------------
# "Not read back" when it was, in effect
# ---------------------------------------------------------------------------


def _readback_gaps(evidence):
    return evidence.gaps("Saved it.", lambda _s: True)


def _written(tmp_path, name="out.md"):
    out = tmp_path / name
    out.write_text("one\n")
    evidence = CompletionEvidence(f"Save it to {out}", str(tmp_path))
    evidence.record("write_file", {"file_path": str(out)}, {"status": "success"}, True)
    return out, evidence


def test_a_skill_tool_that_opened_the_file_read_it_back(tmp_path):
    out, evidence = _written(tmp_path)
    assert any("read back" in gap for gap in _readback_gaps(evidence))

    evidence.expect_skill_read(str(out))
    evidence.record("some-skill/verify", {}, {"status": "errors_found"}, True)

    assert _readback_gaps(evidence) == []


def test_a_failed_check_or_a_later_write_does_not_count(tmp_path):
    out, evidence = _written(tmp_path)
    evidence.expect_skill_read(str(out))
    evidence.record("some-skill/verify", {}, {"status": "error"}, False)
    assert any("read back" in gap for gap in _readback_gaps(evidence))

    evidence.expect_skill_read(str(out))
    evidence.record("some-skill/verify", {}, {"status": "success"}, True)
    evidence.record("write_file", {"file_path": str(out)}, {"status": "success"}, True)
    assert any("read back" in gap for gap in _readback_gaps(evidence))


def test_an_edit_in_place_of_a_file_read_in_full_needs_no_second_read(tmp_path):
    out, evidence = _written(tmp_path)
    evidence.read_by_framework(evidence.key(str(out)))
    assert _readback_gaps(evidence) == []

    for _ in range(3):
        evidence.record(
            "edit_file", {"file_path": str(out)}, {"status": "success"}, True
        )
    assert _readback_gaps(evidence) == []

    # A whole-file rewrite is new text nobody has seen.
    evidence.record("write_file", {"file_path": str(out)}, {"status": "success"}, True)
    assert any("read back" in gap for gap in _readback_gaps(evidence))


def test_an_edit_of_a_file_never_read_in_full_still_needs_a_read(tmp_path):
    out, evidence = _written(tmp_path)
    evidence.record("edit_file", {"file_path": str(out)}, {"status": "success"}, True)
    assert any("read back" in gap for gap in _readback_gaps(evidence))


def test_an_unread_file_is_a_caveat_not_unfinished_work():
    unread = "`out.md` has not been read back completely after its latest write."
    missing = "No successful write to `out.md` is recorded for this turn."

    assert blocking_gaps([unread]) == []
    assert blocking_gaps([unread, missing]) == [missing]
    assert (
        caveated_answer("Saved it.", [unread]) == f"Saved it.\n\nNot checked: {unread}"
    )
    assert "couldn't confirm" in caveated_answer("Saved it.", [unread, missing])


# ---------------------------------------------------------------------------
# Figures a script computed are not "figures with no source"
# ---------------------------------------------------------------------------

_TABLE = "| Group | Total Items | Dropped |\n|---|---|---|\n| All | 48215 | 1930 |\n"
#: Reads its input by a joined path and sets a width: nothing the strict
#: analysis can follow, and nothing that types a figure.
_COMPUTES = (
    "import csv, os\n"
    "source = os.path.join(os.getcwd(), 'items.csv')\n"
    "rows = list(csv.DictReader(open(source)))\n"
    "total = sum(int(r['items']) for r in rows)\n"
    "dropped = sum(int(r['dropped']) for r in rows)\n"
    "width = 22.5\n"
    "open('summary.md', 'w').write(f'| All | {total} | {dropped} |')\n"
)
_TYPES = "open('summary.md', 'w').write('| All | 48215 | 1930 |')\n"


def _ran(code):
    result = {"status": "success", "stdout": "", "stderr": "", "return_code": 0}
    record = verification_record("run_python", {"code": code}, result, errored=False)
    record.update(args={"code": code}, output="", observed=observed_text(result))
    return record


def _findings(code, **kw):
    return file_findings(
        {"summary.md": text_blocks(_TABLE, ".md")},
        [_ran(code)],
        query="Summarise items.csv into summary.md",
        **kw,
    )


def test_figures_a_script_built_into_the_file_are_its_output():
    assert _findings(_COMPUTES), "the strict reading reports them"
    assert _findings(_COMPUTES, built=["summary.md"]) == []


def test_figures_typed_into_the_script_are_still_reported():
    found = _findings(_TYPES, built=["summary.md"])
    assert found and "48215" in found[0].note
