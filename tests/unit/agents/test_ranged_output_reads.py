# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Large results are read in useful pieces, not one small part per call.

Two changes, both bounded by the same tool-result budget:

* ``read_tool_output(entry=n, to_entry=m)`` returns a run of index entries in
  one call, as many whole entries as fit the budget, and says where to go on.
* ``read_file`` on a file too large for one result returns a line-numbered
  outline and the file's first lines, so the next call is a ranged read
  rather than a page through archived parts.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from gaia.agents.base import chunk_index
from gaia.agents.base.artifacts import PAGE_CHARS, ArtifactStore, store_for
from gaia.agents.base.context_eviction import _reread_page
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.file_outline import outline_entries, render_outline
from gaia.security import PathValidator
from tests.unit.agents.test_large_tool_result_truncation import make_agent

THRESHOLD, TARGET = 8000, 6000


def _indexed(sizes):
    """A store holding parts of the given sizes, indexed one entry per part."""
    store = ArtifactStore()
    parts = [chr(ord("a") + i) * size for i, size in enumerate(sizes)]
    text = "".join(parts)
    handle = store.put(text)
    entries, offset = [], 0
    for size in sizes:
        entries.append({"offset": offset, "length": size})
        offset += size
    store.set_index(handle, entries)
    return store, handle, parts


# ---------------------------------------------------------------------------
# A run of entries per call
# ---------------------------------------------------------------------------


def test_a_run_of_entries_comes_back_in_one_call():
    store, handle, parts = _indexed([2000, 2000, 2000, 2000])

    page = store.read(handle, entry=1, to_entry=3, max_chars=20000)

    assert page["content"] == "".join(parts[:3])
    assert page["entry"] == 1 and page["to_entry"] == 3
    assert "next_entry" not in page
    assert page["offset"] == 0


def test_only_whole_entries_that_fit_and_next_entry_says_where_to_go_on():
    store, handle, parts = _indexed([2000, 2000, 2000, 2000])

    page = store.read(handle, entry=1, to_entry=4, max_chars=5000)

    assert page["content"] == "".join(parts[:2])
    assert page["to_entry"] == 2
    assert page["next_entry"] == 3
    follow = store.read(handle, entry=3, to_entry=4, max_chars=5000)
    assert follow["content"] == "".join(parts[2:])


def test_a_run_can_go_past_the_single_page_size():
    store, handle, parts = _indexed([3000] * 5)

    page = store.read(handle, entry=1, to_entry=5, max_chars=20000)

    assert len(page["content"]) == 15000 > PAGE_CHARS
    assert page["content"] == "".join(parts)


def test_a_first_entry_larger_than_the_cap_pages_like_a_single_read():
    store, handle, parts = _indexed([9000, 100])

    page = store.read(handle, entry=1, to_entry=2, max_chars=4000)

    assert page["content"] == parts[0][:4000]
    assert page["remaining"] == 5000
    assert page["next_offset"] == 4000
    # Nothing points past the unread rest of entry 1.
    assert "next_entry" not in page


def test_an_out_of_order_index_stops_the_run():
    store = ArtifactStore()
    handle = store.put("x" * 300)
    store.set_index(
        handle,
        [
            {"offset": 100, "length": 50},
            {"offset": 0, "length": 50},
            {"offset": 200, "length": 50},
        ],
    )

    page = store.read(handle, entry=1, to_entry=3, max_chars=20000)

    assert page["offset"] == 100 and len(page["content"]) == 50
    assert page["to_entry"] == 1 and page["next_entry"] == 2


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"to_entry": 2}, "pass it with entry"),
        ({"entry": 1, "to_entry": 2, "offset": 5}, "pass it with entry"),
        ({"entry": 1, "to_entry": 2, "limit": 5}, "pass it with entry"),
        ({"entry": 3, "to_entry": 2}, "before entry"),
        ({"entry": 1, "to_entry": 9}, "not in the index"),
        ({"entry": 0, "to_entry": 2}, "not in the index"),
        ({"entry": 1, "to_entry": True}, "index entry number"),
        ({"entry": 1, "to_entry": "2"}, "index entry number"),
    ],
)
def test_a_malformed_run_is_refused_with_the_fix(kwargs, match):
    store, handle, _ = _indexed([100, 100, 100])
    with pytest.raises(ValueError, match=match):
        store.read(handle, **kwargs)


def test_single_entry_reads_are_unchanged():
    store, handle, parts = _indexed([9000, 100])

    page = store.read(handle, entry=1)

    assert len(page["content"]) == PAGE_CHARS
    assert "to_entry" not in page and "next_entry" not in page


# ---------------------------------------------------------------------------
# Through the agent's tool
# ---------------------------------------------------------------------------


def _source(functions: int) -> str:
    return "\n".join(textwrap.dedent(f"""
            def handler_{n}(value):
                \"\"\"Handle case {n}.\"\"\"
                total = value * {n}
                return total + {n} - 1
            """) for n in range(functions))


def _agent():
    agent = make_agent(device=None, model_id="fireworks.kimi-k2p7-code")
    agent._truncation_budget = lambda: (THRESHOLD, TARGET)
    return agent


def test_the_tool_reads_a_run_within_the_result_target():
    agent = _agent()
    text = _source(400)
    condensed = agent._condense_tool_result(
        "read_file", {"file_path": "big.py", "content": text}, {}, TARGET
    )
    index = condensed["index"]
    reader = agent._tools_registry["read_tool_output"]["function"]

    page = reader(condensed["artifact"], entry=1, to_entry=index[-1]["n"])

    assert 0 < len(page["content"]) <= TARGET
    start = page["offset"]
    assert text[start : start + len(page["content"])] == page["content"]
    assert page["next_entry"] == page["to_entry"] + 1
    assert page["to_entry"] > 1, "a run returned only one entry"


def test_a_single_entry_read_never_asks_for_the_budget():
    """A host built without the device fields still reads one entry."""
    agent = _agent()

    def _no_budget():
        raise AssertionError("a single-entry read asked for the run budget")

    agent._truncation_budget = _no_budget
    handle = store_for(agent).put("hello")
    agent._register_output_reader()
    reader = agent._tools_registry["read_tool_output"]["function"]
    assert reader(handle, 0, 5)["content"] == "hello"


def test_the_schema_offers_the_run_and_the_index_says_how():
    agent = _agent()
    agent._register_output_reader()
    params = agent._tools_registry["read_tool_output"]["parameters"]
    assert params["to_entry"]["type"] == "integer"
    assert not params["to_entry"]["required"]
    assert "to_entry" in chunk_index.FETCH_HINT


def test_an_evicted_run_is_re_read_as_the_same_run():
    store, handle, _ = _indexed([100, 100, 100])
    page = store.read(handle, entry=1, to_entry=3, max_chars=20000)

    _, call = _reread_page(json.dumps(page), store)

    assert call == f"read_tool_output(artifact={handle}, entry=1, to_entry=3)"


def test_an_evicted_single_page_is_re_read_by_offset_as_before():
    store, handle, _ = _indexed([100, 100, 100])
    page = store.read(handle, entry=2)

    _, call = _reread_page(json.dumps(page), store)

    assert call == f"read_tool_output(artifact={handle}, offset=100, limit=100)"


# ---------------------------------------------------------------------------
# read_file on a file too large for one result
# ---------------------------------------------------------------------------


@pytest.fixture
def host(tmp_path):
    made = FileIOToolsMixin()
    made.path_validator = PathValidator()
    made.path_validator.allowed_paths.add(tmp_path.resolve())
    made._truncation_budget = lambda: (THRESHOLD, TARGET)
    saved = dict(_TOOL_REGISTRY)
    try:
        made.register_file_io_tools()
        made.read = _TOOL_REGISTRY["read_file"]["function"]
        yield made
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_a_large_python_file_comes_back_as_outline_and_head(host, tmp_path):
    path = tmp_path / "big.py"
    path.write_text(
        "class Store:\n    def get(self):\n        return 1\n\n" + _source(300),
        encoding="utf-8",
    )

    result = host.read(str(path))

    assert result["status"] == "success"
    assert len(json.dumps(result, ensure_ascii=False)) <= TARGET
    assert result["start_line"] == 1 and result["end_line"] < result["total_lines"]
    assert result["content"].startswith("     1\tclass Store:")
    assert "     1: class Store" in result["outline"]
    assert result["next_start_line"] == result["end_line"] + 1
    assert f"{result['total_lines']} lines" in result["note"]
    assert "start_line/end_line" in result["note"]
    assert "symbols" not in result


def test_a_file_under_the_cap_is_returned_whole(host, tmp_path):
    path = tmp_path / "small.py"
    path.write_text(_source(5), encoding="utf-8")

    result = host.read(str(path))

    assert result["content"] == path.read_text(encoding="utf-8")
    assert "outline" not in result


def test_a_host_without_a_result_cap_gets_the_whole_file(tmp_path):
    made = FileIOToolsMixin()
    made.path_validator = PathValidator()
    made.path_validator.allowed_paths.add(tmp_path.resolve())
    saved = dict(_TOOL_REGISTRY)
    try:
        made.register_file_io_tools()
        read = _TOOL_REGISTRY["read_file"]["function"]
        path = tmp_path / "big.py"
        path.write_text(_source(300), encoding="utf-8")
        result = read(str(path))
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)
    assert result["content"] == path.read_text(encoding="utf-8")


def test_one_enormous_line_is_left_to_the_condenser(host, tmp_path):
    """Not even line 1 fits: no outline is better than an over-budget one."""
    path = tmp_path / "bundle.min.js"
    path.write_text("var a=" + "1+" * 20000 + "1;", encoding="utf-8")

    result = host.read(str(path))

    assert result["content"] == path.read_text(encoding="utf-8")


def test_a_file_this_turn_wrote_keeps_the_archived_form(host, tmp_path):
    """Its archived pages can prove a full read-back; a line window cannot."""
    from gaia.agents.base.completion import CompletionEvidence

    path = tmp_path / "report.md"
    path.write_text("# Report\n\n" + "A finding.\n" * 2000, encoding="utf-8")
    host._completion_evidence = CompletionEvidence("Write report.md", str(tmp_path))
    host._completion_evidence.record(
        "write_file", {"file_path": str(path)}, {"file_path": str(path)}, True
    )

    result = host.read(str(path))

    assert result["content"] == path.read_text(encoding="utf-8")


def test_a_large_markdown_file_is_outlined_by_heading(host, tmp_path):
    path = tmp_path / "guide.md"
    body = "".join(
        f"## Section {n}\n\n" + "Some prose here.\n" * 40 + "\n" for n in range(30)
    )
    path.write_text("# Guide\n\n```\n# not a heading\n```\n" + body, encoding="utf-8")

    result = host.read(str(path))

    assert "     1: # Guide" in result["outline"]
    assert "## Section 29" in result["outline"]
    assert "not a heading" not in result["outline"]


# ---------------------------------------------------------------------------
# The outline itself
# ---------------------------------------------------------------------------


def test_a_diff_is_outlined_by_file():
    diff = (
        "diff --git a/x.py b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
        "diff --git a/y.ts b/y.ts\n@@ -1 +1 @@\n-c\n+d\n"
    )
    assert [line for line, _, _ in outline_entries(diff, "txt")] == [1, 5]


def test_other_languages_are_outlined_by_declaration():
    source = (
        "import x from 'y';\n"
        "export function load() {}\n"
        "export default class Panel {}\n"
        "const handler = async (e) => {};\n"
        "func (s *Server) Start() error {\n"
        "pub fn parse(input: &str) {}\n"
        "let notADeclaration = 1;\n"
    )
    lines = [line for line, _, _ in outline_entries(source, "ts")]
    assert lines == [2, 3, 4, 5, 6]


def test_python_that_does_not_parse_still_gets_an_outline():
    entries = outline_entries("def ok():\n    pass\ndef broken(:\n", "python")
    assert [line for line, _, _ in entries] == [1, 3]


def test_methods_are_dropped_before_top_level_entries():
    entries = [(1, 0, "class A")] + [(n, 1, f"def m{n}") for n in range(2, 60)]
    entries += [(60, 0, "def last")]
    text = render_outline(entries, 120)
    assert "class A" in text and "def last" in text
    assert "def m2" not in text


def test_an_outline_over_budget_samples_the_whole_file():
    entries = [(n, 0, f"def f{n}") for n in range(1, 501)]
    text = render_outline(entries, 400)
    assert len(text) <= 400
    rows = text.splitlines()
    assert rows[-1].startswith("... 1 in ") and "of 500 entries" in rows[-1]
    assert int(rows[-2].split(":")[0]) > 400, "the sample stopped short of the end"


def test_a_tiny_budget_still_returns_an_outline():
    entries = [(n, 0, "def " + "x" * 90) for n in range(1, 50)]
    assert len(render_outline(entries, 200).splitlines()) >= 1
