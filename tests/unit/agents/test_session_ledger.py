# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The session findings ledger records what exploration found, deterministically,
and hands any of it back exactly through the artifact store."""

# pylint: disable=protected-access

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gaia.agents.base.artifacts import ArtifactStore, store_for
from gaia.agents.base.context_eviction import NEVER_EVICTED_TOOLS, ContextEvictor
from gaia.agents.base.session_ledger import (
    DIGEST_HEADER,
    LEDGER_TOOL,
    TOOL_CHARS,
    SessionLedger,
    ledger_for,
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon-home"))


def _python_module(functions: int = 24) -> str:
    body = ['"""A module with enough structure to outline."""', "import os", ""]
    for i in range(functions):
        body += [
            f"def func_{i}(x):",
            f'    """Function number {i}."""',
            "    total = 0",
            "    for k in range(x):",
            "        total += k * " + str(i),
            "    return total",
            "",
        ]
    body += ["class Widget:", "    def size(self):", "        return 1", ""]
    return "\n".join(body)


def _owner() -> SimpleNamespace:
    return SimpleNamespace()


def _read_result(path: str, text: str) -> dict:
    return {
        "status": "success",
        "file_path": path,
        "content": text,
        "file_type": "python",
        "line_count": text.count("\n"),
    }


def _fill(ledger: SessionLedger) -> None:
    """The same findings every time, so two ledgers can be compared."""
    text = _python_module()
    ledger.record(
        "read_file",
        {"file_path": "src/a.py"},
        _read_result("src/a.py", text),
        1,
        "parent",
    )
    ledger.record(
        "read_file",
        {"file_path": "README.md"},
        {"status": "success", "file_path": "README.md", "content": "# Hi\nshort\n"},
        2,
        "parent",
    )
    ledger.record(
        "search_file_content",
        {"pattern": "func_1"},
        {
            "status": "success",
            "pattern": "func_1",
            "matches": [
                {"file": "src/a.py", "line": 8, "content": "def func_1"},
                {"file": "src/a.py", "line": 80, "content": "def func_10"},
                {"file": "src/b.py", "line": 3, "content": "func_1()"},
            ],
            "total_matches": 3,
        },
        3,
        "worker:investigate",
    )
    ledger.record(
        "run_shell_command",
        {"command": "cd /repo && pytest tests/unit -q"},
        {
            "status": "success",
            "stdout": "..\n2 passed in 0.10s\n",
            "stderr": "",
            "return_code": 0,
            "check_result": {
                "label": "pytest",
                "target": "pytest tests/unit -q",
                "kind": "test",
                "passed": True,
                "summary": "2 passed in 0.10s",
            },
        },
        4,
        "worker:verify",
    )
    ledger.record(
        "write_file",
        {"file_path": "src/b.py", "content": "x"},
        {"status": "success", "file_path": "src/b.py"},
        5,
        "worker:implement",
    )
    ledger.add_finding(
        "investigate", "where is func_1 used", "In src/b.py:3.", 6, "worker:investigate"
    )


# ---------------------------------------------------------------------------


def test_read_is_outlined_with_line_ranges_and_fetches_back_exactly():
    owner = _owner()
    ledger = ledger_for(owner)
    text = _python_module()
    entry = ledger.record(
        "read_file",
        {"file_path": "src/a.py"},
        _read_result("src/a.py", text),
        3,
        "parent",
    )
    assert entry.kind == "read" and entry.key == "src/a.py"
    assert entry.size == len(text) and entry.step == 3 and entry.actor == "parent"
    labels = [e["label"] for e in entry.outline]
    assert any("func_0" in label for label in labels)
    assert any("Widget" in label for label in labels)
    assert entry.outline[0]["lines"][0] == 1
    # Line ranges tile the file in order.
    for previous, current in zip(entry.outline, entry.outline[1:]):
        assert current["lines"][0] == previous["lines"][1] + 1
    assert entry.outline[-1]["lines"][1] == len(text.splitlines())
    # Every entry number reads back exactly that part of the file.
    store = store_for(owner)
    for part in entry.outline:
        page = store.read(entry.artifact, entry=part["n"])
        assert page["content"] == text[part["offset"] : part["offset"] + part["length"]]


def test_short_read_keeps_path_size_and_whole_text_only():
    owner = _owner()
    entry = ledger_for(owner).record(
        "read_file",
        {"file_path": "notes.txt"},
        {"status": "success", "file_path": "notes.txt", "content": "one\ntwo\n"},
        1,
        "parent",
    )
    assert entry.size == 8
    assert [e["n"] for e in entry.outline] == [1]
    assert entry.outline[0]["lines"] == [1, 2]
    assert store_for(owner).read(entry.artifact, entry=1)["content"] == "one\ntwo\n"


def test_reread_replaces_the_outline_in_place():
    ledger = ledger_for(_owner())
    first = _python_module(20)
    second = _python_module(30)
    ledger.record("read_file", {}, _read_result("src/a.py", first), 1, "parent")
    ledger.record("read_file", {}, _read_result("src/z.py", first), 2, "parent")
    ledger.record(
        "read_file", {}, _read_result("src/a.py", second), 7, "worker:implement"
    )
    assert [e.key for e in ledger.entries] == ["src/a.py", "src/z.py"]
    entry = ledger.entries[0]
    assert entry.size == len(second) and entry.step == 7
    assert entry.actor == "worker:implement"
    assert any("func_28" in e["label"] for e in entry.outline)


def test_paging_shell_commands_are_reads():
    ledger = ledger_for(_owner())
    text = _python_module()
    lines = text.split("\n")
    page = "\n".join(lines[9:40]) + "\n"
    entry = ledger.record(
        "run_shell_command",
        {"command": "cd /repo && sed -n '10,40p' src/a.py"},
        {"status": "success", "stdout": page, "stderr": "", "return_code": 0},
        2,
        "parent",
    )
    assert entry.kind == "read" and entry.key == "src/a.py" and entry.partial
    assert entry.outline[0]["lines"][0] == 10
    whole = ledger.record(
        "run_shell_command",
        {"command": "cat src/a.py"},
        {"status": "success", "stdout": text, "stderr": "", "return_code": 0},
        3,
        "parent",
    )
    assert whole.partial is False and whole.outline[0]["lines"][0] == 1
    assert any("func_11" in e["label"] for e in whole.outline)
    tail = ledger.record(
        "run_shell_command",
        {"command": "tail -n 5 src/a.py"},
        {"status": "success", "stdout": "x\n", "stderr": "", "return_code": 0},
        4,
        "parent",
    )
    assert tail.partial and tail.outline[0]["lines"] is None
    # Two files, or a failing command, is not a read.
    assert (
        ledger.record(
            "run_shell_command",
            {"command": "cat a.py b.py"},
            {"status": "success", "stdout": "x", "stderr": "", "return_code": 0},
            5,
            "parent",
        )
        is None
    )


def test_searches_record_pattern_and_per_file_hit_counts():
    ledger = ledger_for(_owner())
    _fill(ledger)
    tool = next(e for e in ledger.entries if e.kind == "search")
    assert tool.key == "func_1" and tool.label == "search_file_content"
    assert tool.hits == [("src/a.py", 2), ("src/b.py", 1)] and tool.size == 3
    grep = ledger.record(
        "run_shell_command",
        {"command": "grep -rn -A 2 'retry' src/ | head -50"},
        {
            "status": "success",
            "stdout": "src/x.py:10:retry = 3\nsrc/x.py-11-pass\nsrc/y.py:4:retry()\n",
            "stderr": "",
            "return_code": 0,
        },
        4,
        "worker:investigate",
    )
    assert grep.key == "retry" and grep.label == "grep"
    assert grep.hits == [("src/x.py", 1), ("src/y.py", 1)]
    listed = ledger.record(
        "run_shell_command",
        {"command": "rg -l -e 'foo' src"},
        {"status": "success", "stdout": "src/a.py\nsrc/b.py\n", "return_code": 0},
        5,
        "parent",
    )
    assert listed.key == "foo" and [f for f, _ in listed.hits] == [
        "src/a.py",
        "src/b.py",
    ]
    none = ledger.record(
        "run_shell_command",
        {"command": "grep -rn nothing src"},
        {"status": "success", "stdout": "", "stderr": "", "return_code": 1},
        6,
        "parent",
    )
    assert none.kind == "search" and none.hits == [] and none.size == 0


def test_checks_record_the_runner_summary_from_either_source():
    ledger = ledger_for(_owner())
    _fill(ledger)
    check = next(e for e in ledger.entries if e.kind == "check")
    assert check.key == "pytest tests/unit -q" and check.label == "pytest"
    assert check.passed is True and check.summary == "2 passed in 0.10s"
    snippet = ledger.record(
        "run_python",
        {"code": "import pytest; pytest.main(['-q'])"},
        {
            "status": "success",
            "stdout": "1 failed, 2 passed in 0.5s\n",
            "return_code": 0,
        },
        9,
        "worker:verify",
    )
    assert snippet.kind == "check" and snippet.passed is False
    assert snippet.summary == "1 failed, 2 passed in 0.5s"
    assert (
        ledger.record(
            "run_shell_command",
            {"command": "ls"},
            {"status": "success", "stdout": "a\nb\n", "stderr": "", "return_code": 0},
            10,
            "parent",
        )
        is None
    )


def test_changes_and_worker_findings_are_recorded():
    ledger = ledger_for(_owner())
    _fill(ledger)
    change = next(e for e in ledger.entries if e.kind == "change")
    assert change.key == "src/b.py" and change.actor == "worker:implement"
    assert change.step == 5 and change.label == "write_file"
    finding = next(e for e in ledger.entries if e.kind == "finding")
    assert finding.key == "where is func_1 used" and finding.label == "investigate"
    assert finding.summary == "In src/b.py:3."
    # An errored result records nothing; a delegate result is the parent's view.
    assert (
        ledger.record("read_file", {}, {"status": "error", "error": "x"}, 1, "p")
        is None
    )
    assert ledger.record("delegate_task", {}, {"status": "success"}, 1, "p") is None
    assert ledger.record(LEDGER_TOOL, {}, {"status": "success"}, 1, "p") is None


def test_digest_is_deterministic_and_capped():
    first, second = ledger_for(_owner()), ledger_for(_owner())
    _fill(first)
    _fill(second)
    assert first.render() == second.render()
    digest = first.render()
    assert "output_" not in digest
    for line in (
        "Files read:",
        "- src/a.py (",
        "parent, step 1",
        "Searches:",
        "'func_1' (search_file_content; 3 hits in 2 files; worker:investigate, step 3): src/a.py x2, src/b.py x1",
        "Checks run:",
        "- pytest tests/unit -q (pytest; worker:verify, step 4): passed — 2 passed in 0.10s",
        "Files changed:",
        "- src/b.py (write_file; worker:implement, step 5)",
        "Worker findings:",
        "- [investigate] where is func_1 used (worker:investigate, step 6): In src/b.py:3.",
    ):
        assert line in digest, line
    assert "L1-" in digest and "func_0" in digest
    assert len(digest) <= 6000
    small = first.render(max_chars=600)
    assert len(small) <= 600
    assert f"more ({LEDGER_TOOL}(query) lists them)" in small or "…" in small
    assert first.render(max_chars=600) == second.render(max_chars=600)
    assert SessionLedger(_owner()).render() == "(nothing recorded yet)"


def test_query_returns_handle_and_entry_numbers_that_read_back_the_text():
    owner = _owner()
    ledger = ledger_for(owner)
    _fill(ledger)
    text = _python_module()
    result = ledger.tool_result("func_5")
    assert result["status"] == "success" and result["total"] >= 1
    read = next(m for m in result["matches"] if m["kind"] == "read")
    assert read["path"] == "src/a.py" and read["artifact"].startswith("output_")
    part = next(e for e in read["entries"] if "func_5" in e["label"])
    page = store_for(owner).read(read["artifact"], entry=part["n"])
    assert page["content"].startswith("def func_5(x):")
    assert page["content"] in text
    assert ledger.tool_result("")["findings"] == ledger.render(TOOL_CHARS - 200)
    assert ledger.tool_result("nope")["matches"] == []
    assert len(json.dumps(ledger.tool_result("src"), ensure_ascii=False)) <= TOOL_CHARS
    with pytest.raises(ValueError):
        ledger.tool_result(3)


def test_tool_result_stays_under_its_cap_and_names_the_omitted():
    ledger = ledger_for(_owner())
    text = _python_module(40)
    for i in range(30):
        ledger.record("read_file", {}, _read_result(f"src/m{i}.py", text), i, "parent")
    result = ledger.tool_result("src/m")
    assert len(json.dumps(result, ensure_ascii=False)) <= TOOL_CHARS
    assert result["omitted"] > 0 and result["total"] == 30
    assert result["omitted"] + len(result["matches"]) == 30


def test_brief_block_opens_with_the_fixed_header():
    ledger = ledger_for(_owner())
    assert ledger.brief_block() == f"{DIGEST_HEADER}\n(nothing recorded yet)"
    _fill(ledger)
    assert ledger.brief_block().startswith(f"{DIGEST_HEADER}\nFiles read:\n")


def test_entries_survive_eviction_of_the_result_that_produced_them():
    owner = _owner()
    store = store_for(owner)
    ledger = ledger_for(owner)
    text = _python_module()
    result = _read_result("src/a.py", text)
    entry = ledger.record("read_file", {"file_path": "src/a.py"}, result, 1, "parent")
    messages = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "name": "read_file",
            "content": json.dumps(result),
        },
    ]
    evictor = ContextEvictor(threshold_tokens=1, keep_steps=0, min_batch_tokens=1)
    evictor.live_tokens = 10**6
    assert evictor.evict(messages, step=5, store=store) is not None
    assert "[evicted:" in messages[2]["content"][0]["text"]
    assert ledger.entries == [entry]
    assert (
        store.read(entry.artifact, entry=1)["content"]
        == text[: entry.outline[0]["length"]]
    )
    assert LEDGER_TOOL in NEVER_EVICTED_TOOLS


def test_ledger_for_is_one_per_owner_and_uses_the_owners_current_store():
    owner = _owner()
    ledger = ledger_for(owner)
    assert ledger_for(owner) is ledger
    owner._output_artifacts = ArtifactStore()
    entry = ledger.record(
        "read_file", {}, {"status": "success", "file_path": "a", "content": "z"}, 1, "p"
    )
    assert owner._output_artifacts.has(entry.artifact)
