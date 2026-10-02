# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""read_file reads code the way a search reports it: by line number.

A search hit says ``similar.py:398``; the agent could only ask for a
character page, and a large file read whole overflowed the result budget and
came back as pages to walk one call at a time.
"""

import pytest

from gaia.agents.base.completion import CompletionEvidence
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import MAX_READ_LINES, FileIOToolsMixin
from gaia.security import PathValidator


@pytest.fixture
def read_file(tmp_path):
    host = FileIOToolsMixin()
    host.path_validator = PathValidator()
    host.path_validator.allowed_paths.add(tmp_path.resolve())
    host._truncation_budget = lambda: (3000, 2000)
    saved = dict(_TOOL_REGISTRY)
    try:
        host.register_file_io_tools()
        yield _TOOL_REGISTRY["read_file"]["function"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _code(tmp_path, lines):
    path = tmp_path / "mod.py"
    path.write_text(
        "\n".join(f"def f{i}():\n    return {i}" for i in range(lines // 2)),
        encoding="utf-8",
    )
    return path


def test_a_range_comes_back_numbered(read_file, tmp_path):
    path = _code(tmp_path, 20)
    result = read_file(str(path), start_line=3, end_line=4)
    assert result["status"] == "success"
    assert result["content"] == "     3\tdef f1():\n     4\t    return 1\n"
    assert (result["start_line"], result["end_line"]) == (3, 4)
    assert result["total_lines"] == 20
    assert result["next_start_line"] == 5


def test_the_last_window_says_there_is_no_more(read_file, tmp_path):
    result = read_file(str(_code(tmp_path, 10)), start_line=9)
    assert result["end_line"] == 10 and "next_start_line" not in result


def test_a_range_is_capped_in_lines_and_chars(read_file, tmp_path):
    path = tmp_path / "long.txt"
    path.write_text("\n".join(f"line {i}" for i in range(1000)), encoding="utf-8")
    result = read_file(str(path), start_line=1, end_line=1000)
    assert result["end_line"] <= MAX_READ_LINES
    assert len(result["content"]) <= 2000
    assert result["next_start_line"] == result["end_line"] + 1


@pytest.mark.parametrize(
    "start,end,message",
    [(0, 5, "1-based"), (50, None, "past the end"), (8, 3, "before start_line")],
)
def test_a_bad_range_says_what_is_wrong(read_file, tmp_path, start, end, message):
    result = read_file(str(_code(tmp_path, 20)), start_line=start, end_line=end)
    assert result["status"] == "error" and message in result["error"]


def test_lines_and_characters_are_not_mixed(read_file, tmp_path):
    result = read_file(str(_code(tmp_path, 20)), offset=10, start_line=1)
    assert result["status"] == "error" and "not both" in result["error"]


def test_a_small_file_still_comes_back_whole(read_file, tmp_path):
    path = _code(tmp_path, 10)
    result = read_file(str(path))
    assert result["content"] == path.read_text(encoding="utf-8")
    assert "start_line" not in result


def test_a_large_file_still_comes_back_whole_for_the_chunk_index(read_file, tmp_path):
    """Whole-file reads are unchanged: the chunk index pages large results."""
    path = _code(tmp_path, 400)
    result = read_file(str(path))
    assert result["content"] == path.read_text(encoding="utf-8")
    assert "start_line" not in result


def test_a_line_window_is_never_a_full_readback(tmp_path):
    """Numbered text is not the file's bytes, so it cannot prove a write."""
    ledger = CompletionEvidence("Save the summary to `summary.md`", str(tmp_path))
    path = "summary.md"
    ledger.record("write_file", {"file_path": path}, {"file_path": path}, True)
    window = {
        "file_path": path,
        "content": "     1\tobserved\n",
        "start_line": 1,
        "end_line": 1,
        "total_lines": 1,
    }
    ledger.delivered("read_file", {"file_path": path}, window, window)
    assert "read back" in " ".join(ledger.gaps("Done.", lambda s: False))
    whole = {"file_path": path, "content": "observed"}
    ledger.delivered("read_file", {"file_path": path}, whole, whole)
    assert ledger.gaps("Done.", lambda s: False) == []
