# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A large tool result reaches the model as ``shown`` + a structural ``index``.

On a real 144K-line repository 83% of the agent's conversation was accumulated
tool results, re-sent on every step. These tests pin that an over-budget
result is split along its own structure -- functions, headings, JSON records,
a search's files, test sections -- that every indexed part reads back exactly
through ``read_tool_output``, and that at a small budget the mechanism cuts
the characters a long task re-sends by at least half.
"""

from __future__ import annotations

import ast
import json
import random
import textwrap
from unittest.mock import MagicMock

import pytest

from gaia.agents.base import chunk_index
from gaia.agents.base.artifacts import ArtifactStore, store_for
from gaia.llm.lemonade_client import truncation_budget
from tests.unit.agents.test_large_tool_result_truncation import make_agent

CLOUD_MODEL = "fireworks.kimi-k2p7-code"
#: Small enough that realistic inputs condense; indexing does not depend on
#: which profile set the budget.
THRESHOLD, TARGET = 8000, 6000


def small_budget_agent():
    agent = make_agent(device=None, model_id=CLOUD_MODEL)
    agent._truncation_budget = lambda: (THRESHOLD, TARGET)
    return agent


def assert_tiles(text, chunks, *, line_aligned=True):
    """Chunks are in order, contiguous, exact, and never start mid-line."""
    assert chunks, "no chunks"
    assert chunks[0].offset == 0
    for prev, nxt in zip(chunks, chunks[1:]):
        assert prev.end == nxt.offset
    assert chunks[-1].end == len(text)
    assert "".join(text[c.offset : c.offset + c.length] for c in chunks) == text
    for c in chunks:
        assert c.length > 0
        assert "\n" not in c.label
        if line_aligned and c.offset:
            assert text[c.offset - 1] == "\n", f"{c.label!r} starts mid-line"


def read_back(agent, result, entry):
    page = agent._tools_registry["read_tool_output"]["function"](
        result["artifact"], entry["offset"], entry["length"]
    )
    return page["content"]


# ---------------------------------------------------------------------------
# Realistic inputs
# ---------------------------------------------------------------------------


def python_module(n_classes=4, methods=6, functions=10) -> str:
    """A realistic multi-class module, ~25 KB at the defaults."""
    parts = [
        '"""Inventory service: carts, pricing rules and the checkout pipeline."""\n',
        "\nfrom __future__ import annotations\n\nimport json\nimport logging\n"
        "from dataclasses import dataclass\nfrom typing import Dict, List\n\n"
        "log = logging.getLogger(__name__)\n\nMAX_ITEMS = 500\n",
    ]
    for c in range(n_classes):
        parts.append(
            f"\n\n# ---------------------------------------------------------------\n"
            f"# Section {c}\n"
            f"# ---------------------------------------------------------------\n"
            f"\n\n@dataclass\nclass Service{c}(object):\n"
            f'    """Service {c} keeps state for one checkout stage."""\n\n'
            f"    capacity: int = {c + 10}\n"
        )
        for m in range(methods):
            body = "".join(
                f"        total_{k} = sum(item.price * item.quantity for item in items if item.sku != 'x{k}')\n"
                for k in range(6)
            )
            parts.append(
                f"\n    def method_{m}(self, items: List[dict], limit: int = {m}) -> int:\n"
                f'        """Compute total {m} for the given items."""\n'
                f"{body}"
                f"        return total_0 + limit\n"
            )
    for f in range(functions):
        body = "".join(
            f"    value = data.get('key_{k}', {k}) * {f + 1}\n    log.debug('step %s', value)\n"
            for k in range(8)
        )
        parts.append(
            f"\n\n@staticmethod\ndef helper_{f}(data: Dict[str, int], *, strict: bool = False) -> int:\n"
            f'    """Helper {f}: fold the data into one number."""\n'
            f"{body}    return value\n"
        )
    parts.append('\n\nif __name__ == "__main__":\n    print(helper_0({}))\n')
    return "".join(parts)


MARKDOWN = textwrap.dedent("""\
    Intro paragraph before any heading.

    # Build guide

    Some text about building.

    ## Prerequisites

    - cmake
    - ninja

    ```bash
    # not a heading, it is inside a fence
    cmake -B build
    ```

    ## Configure

    Run the configure step.

    ### Presets

    Presets live in CMakePresets.json.

    # Testing

    Run ctest.
    """)


def search_result(n_matches=100, n_files=12) -> dict:
    matches = []
    for i in range(n_matches):
        f = i % n_files
        matches.append(
            {
                "file": f"/repo/src/pkg_{f}/module_{f}.py",
                "line": 10 + i,
                "content": f"gfx90a_target_{i} = resolve('gfx90a', " + "x" * 150 + ")",
                "context": [f"    context line {i}.{k} " + "y" * 150 for k in range(3)],
            }
        )
    matches.sort(key=lambda m: (m["file"], m["line"]))
    return {
        "status": "success",
        "pattern": "gfx90a",
        "matches": matches,
        "total_matches": n_matches,
        "files_searched": 853,
        "message": f"Found {n_matches} matches in 853 files",
    }


def pytest_log() -> str:
    lines = [
        "============================= test session starts =============================="
    ]
    lines += [
        "platform darwin -- Python 3.12.0, pytest-8.3.0",
        "collected 42 items",
        "",
    ]
    lines += ["tests/test_cart.py " + "." * 40 + "FF" + "  [100%]", ""]
    lines += [
        "=================================== FAILURES ==================================="
    ]
    for name in ("test_total_rounds_down", "test_discount_applies_once"):
        lines += [
            f"_____________________________ {name} _____________________________",
            "",
        ]
        lines += [f"    def {name}():"]
        lines += [f"        cart = Cart(items=[{k}, {k + 1}])" for k in range(40)]
        lines += ["        assert cart.total() == 10", "E       assert 11 == 10", ""]
        lines += ["tests/test_cart.py:88: AssertionError", ""]
    lines += [
        "=========================== short test summary info ============================"
    ]
    lines += ["FAILED tests/test_cart.py::test_total_rounds_down - assert 11 == 10"]
    lines += ["FAILED tests/test_cart.py::test_discount_applies_once - assert 11 == 10"]
    lines += [
        "========================= 2 failed, 40 passed in 0.52s ========================="
    ]
    return "\n".join(lines) + "\n"


CHECK = {
    "label": "pytest",
    "target": "pytest tests/test_cart.py",
    "kind": "test",
    "passed": False,
    "summary": "2 failed, 40 passed in 0.52s",
}


def shell_result(stdout: str) -> dict:
    return {
        "status": "success",
        "command": "pytest tests/test_cart.py",
        "stdout": stdout,
        "stderr": "",
        "return_code": 1,
        "has_errors": True,
        "duration_seconds": 0.6,
        "timeout": 60,
        "cwd": "/repo",
        "output_truncated": False,
        "steps": [{"command": "pytest tests/test_cart.py", "return_code": 1}],
        "check_result": dict(CHECK),
    }


# ---------------------------------------------------------------------------
# Each chunker tiles its text exactly
# ---------------------------------------------------------------------------


def _random_python(rng: random.Random) -> str:
    out = []
    if rng.random() < 0.7:
        out.append('"""Module doc λ."""\nimport os\n')
    for i in range(rng.randint(1, 8)):
        pick = rng.random()
        if pick < 0.4:
            deco = "@decorator\n" if rng.random() < 0.3 else ""
            comment = "# explains f\n" if rng.random() < 0.3 else ""
            out.append(f"\n{comment}{deco}def f{i}(a, b=1):\n    return a + b\n")
        elif pick < 0.7:
            out.append(f"\nclass C{i}:\n    x = 1\n")
            for m in range(rng.randint(0, 3)):
                out.append(f"\n    def m{m}(self):\n        return {m}\n")
        else:
            out.append(f"\nVALUE_{i} = {i}\n")
    return "".join(out)


def _random_lines(rng: random.Random, vocab) -> str:
    return "".join(rng.choice(vocab) + "\n" for _ in range(rng.randint(1, 120)))


_VOCAB = [
    "",
    "",
    "plain line of text with ünïcode",
    "# Heading",
    "## Sub heading",
    "```",
    "==== test session starts ====",
    "FAILED tests/a.py::t - boom",
    "Traceback (most recent call last):",
    "src/a.py:12: match here",
    "    indented continuation",
    "--- a/x.py",
    "+++ b/x.py",
    "@@ -1,3 +1,4 @@",
    "x" * 300,
]


@pytest.mark.parametrize("seed", range(40))
def test_every_chunker_tiles_random_text_exactly(seed):
    rng = random.Random(seed)
    text = _random_lines(rng, _VOCAB)
    for kind in ("markdown", "output", "diff", "text"):
        chunks = chunk_index.chunk_text(text, kind)
        if chunks:
            assert_tiles(text, chunks)
    for chunker in (
        chunk_index.chunk_markdown,
        chunk_index.chunk_output,
        chunk_index.chunk_diff,
        chunk_index.chunk_paragraphs,
        chunk_index.chunk_lines,
    ):
        chunks = chunker(text)
        if chunks:
            assert_tiles(text, chunks)
            assert_tiles(text, chunk_index.split_oversized(text, chunks))

    source = _random_python(rng)
    chunks = chunk_index.chunk_python(source)
    if chunks:
        assert_tiles(source, chunks)

    records = [
        {"id": i, "name": "ñ" * rng.randint(0, 40)} for i in range(rng.randint(0, 70))
    ]
    doc = {"items": records, "meta": {"n": len(records)}, "note": "x"}
    for rendered, aligned in (
        (json.dumps(doc, indent=1, ensure_ascii=False), True),
        (json.dumps(doc), False),
        (json.dumps(records, indent=2), True),
    ):
        chunks = chunk_index.chunk_json(rendered)
        if chunks:
            assert_tiles(rendered, chunks, line_aligned=aligned)

    result = search_result(rng.randint(1, 60), rng.randint(1, 9))
    text, chunks = chunk_index.render_search(result["matches"])
    assert_tiles(text, chunks)


def test_python_chunks_follow_definitions_and_carry_signature_and_doc():
    source = python_module(n_classes=2, methods=2, functions=2)
    chunks = chunk_index.chunk_python(source)
    assert_tiles(source, chunks)
    labels = [c.label for c in chunks]
    assert labels[0].startswith("L1-") and "Inventory service" in labels[0]
    assert "(5 imports)" in labels[0]
    assert any(
        "class Service0(object) — Service 0 keeps state" in lab for lab in labels
    )
    method = next(lab for lab in labels if "def Service1.method_1(" in lab)
    assert "limit: int=1" in method and "Compute total 1" in method
    helper = next(c for c in chunks if "def helper_1(" in c.label)
    body = source[helper.offset : helper.end]
    assert body.startswith("@staticmethod\ndef helper_1(")
    assert ast.parse(body).body[0].name == "helper_1"
    assert any("module code: if __name__" in lab for lab in labels)
    # The comment banner above the class belongs to the class's chunk.
    service = next(c for c in chunks if "class Service1" in c.label)
    assert source[service.offset :].startswith("# ----")


def test_markdown_chunks_by_heading_and_ignores_fenced_hashes():
    chunks = chunk_index.chunk_markdown(MARKDOWN)
    assert_tiles(MARKDOWN, chunks)
    labels = [c.label.split(" ", 1)[1] for c in chunks]
    assert labels == [
        "Intro paragraph before any heading.",
        "# Build guide",
        "## Prerequisites",
        "## Configure",
        "### Presets",
        "# Testing",
    ]


def test_json_groups_records_and_names_them():
    doc = {"status": "ok", "messages": [{"id": f"m{i}", "n": i} for i in range(45)]}
    text = json.dumps(doc, indent=1)
    chunks = chunk_index.chunk_json(text)
    assert_tiles(text, chunks)
    labels = [c.label for c in chunks]
    assert labels[0].startswith('"status": ok')
    assert labels[1:] == [
        '"messages" 0–19: id=m0, n=0',
        '"messages" 20–39: id=m20, n=20',
        '"messages" 40–44: id=m40, n=40',
    ]
    assert text[chunks[2].offset :].lstrip().startswith('{\n   "id": "m20"')


def test_search_groups_by_file_and_leads_with_each_files_first_match():
    result = search_result()
    text, chunks = chunk_index.render_search(result["matches"])
    assert_tiles(text, chunks)
    leads = [c for c in chunks if c.lead]
    assert len(leads) == 12
    assert leads[0].label.startswith(
        "pkg_0/module_0.py: 9 matches, L10: gfx90a_target_0"
    )
    assert any(": 8 more (lines" in c.label for c in chunks)
    # Nothing is lost in the rendering: every match line and context is there.
    for match in result["matches"]:
        assert f"  {match['line']}: {match['content']}\n" in text
        assert all(f"    | {line}\n" in text for line in match["context"])


def test_search_labels_keep_the_separator_the_paths_used():
    """A POSIX path must not be renamed with backslashes on Windows."""
    matches = [
        {"file": "/repo/src/a/one.py", "line": 1, "content": "hit"},
        {"file": "/repo/src/b/two.py", "line": 2, "content": "hit"},
    ]
    _, chunks = chunk_index.render_search(matches)
    assert [c.label.split(":")[0] for c in chunks] == ["a/one.py", "b/two.py"]
    assert not any("\\" in c.label for c in chunks)


def test_output_sections_follow_test_markers_and_failures_lead():
    log = pytest_log()
    chunks = chunk_index.chunk_output(log)
    assert_tiles(log, chunks)
    labels = [c.label for c in chunks]
    assert any("test_total_rounds_down" in lab for lab in labels)
    assert any("short test summary info" in lab for lab in labels)
    assert chunks[-1].lead, "the run's summary must be offered first"
    failing = next(c for c in chunks if "test_discount_applies_once" in c.label)
    assert failing.lead


def test_unstructured_label_seam_returns_the_first_line():
    assert chunk_index.label_for_unstructured("\n\n  first real line \nsecond") == (
        "first real line"
    )
    assert chunk_index.label_for_unstructured("\n \n") == "(blank)"


def test_single_line_text_has_no_structure_to_index():
    assert chunk_index.chunk_text("x" * 50000, "text") == []


# ---------------------------------------------------------------------------
# Through the agent's handler
# ---------------------------------------------------------------------------


@pytest.fixture
def read_file_tool(tmp_path):
    from gaia.agents.base.tools import _TOOL_REGISTRY
    from gaia.agents.tools.file_io_tools import FileIOToolsMixin
    from gaia.security import PathValidator

    mixin = FileIOToolsMixin()
    mixin.console = None
    mixin.path_validator = PathValidator(allowed_paths=[str(tmp_path)])
    mixin._validate_python_syntax = MagicMock(
        return_value={"is_valid": True, "errors": []}
    )
    mixin._parse_python_code = MagicMock()
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        mixin.register_file_io_tools()
        yield _TOOL_REGISTRY["read_file"]["function"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_a_25kb_python_read_is_indexed_by_function_and_reads_back_exactly(
    tmp_path, read_file_tool
):
    source = python_module()
    assert 24000 < len(source) < 30000
    path = tmp_path / "inventory.py"
    path.write_text(source, encoding="utf-8")
    original = read_file_tool(str(path))
    agent = small_budget_agent()
    conversation = []

    result = agent._handle_large_tool_result(
        "read_file", original, conversation, {"file_path": str(path)}
    )

    assert conversation[-1]["content"] is result
    assert len(json.dumps(result, ensure_ascii=False)) <= TARGET
    assert result["line_count"] == original["line_count"]
    assert result["size_bytes"] == original["size_bytes"]
    assert result["file_path"] == str(path)
    assert result["archived_field"] == "content"
    assert result["total_chars"] == len(source)
    assert "content" not in result and "symbols" not in result
    shown_text = "".join(s["text"] for s in result["shown"])
    assert result["omitted_chars"] == len(source) - len(shown_text)

    # Every definition is either shown whole or has its own index entry whose
    # span reads back as exactly its source.
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    defs = [(n.name, n) for n in tree.body if isinstance(n, ast.FunctionDef)]
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        defs += [
            (f"{cls.name}.{m.name}", m)
            for m in cls.body
            if isinstance(m, ast.FunctionDef)
        ]
    assert len(defs) == 34
    indexed = 0
    for qualname, node in defs:
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        exact = "".join(lines[start - 1 : node.end_lineno])
        entry = next(
            (
                e
                for e in result["index"]
                if e["label"].endswith(f" {qualname}") or f" {qualname}(" in e["label"]
            ),
            None,
        )
        if entry is None:
            assert exact in shown_text, f"{qualname} is neither shown nor indexed"
            continue
        indexed += 1
        assert f"L{start}-" in entry["label"]
        assert read_back(agent, result, entry).rstrip("\n") == exact.rstrip("\n")
    assert indexed > 25, "most of the file should be reachable through the index"

    # The index plus what is shown is the whole file.
    spans = sorted(
        [(e["offset"], e["length"]) for e in result["index"]]
        + [(s["offset"], len(s["text"])) for s in result["shown"]]
    )
    assert sum(length for _, length in spans) == len(source)
    assert all(a + la == b for (a, la), (b, _) in zip(spans, spans[1:]))


def test_check_result_survives_condensation_verbatim():
    agent = small_budget_agent()
    original = shell_result(pytest_log() * 3)

    result = agent._handle_large_tool_result(
        "run_shell_command", original, [], {"command": "pytest tests/test_cart.py"}
    )

    assert len(json.dumps(result, ensure_ascii=False)) <= TARGET
    assert result["check_result"] == CHECK
    assert result["return_code"] == 1 and result["archived_field"] == "stdout"
    shown = "".join(s["text"] for s in result["shown"])
    assert "2 failed, 40 passed" in shown, "the run's summary was not kept in view"
    for entry in result["index"]:
        assert (
            read_back(agent, result, entry)
            == original["stdout"][entry["offset"] : entry["offset"] + entry["length"]]
        )


@pytest.mark.parametrize("copies", [3, 20, 60, 200])
def test_a_long_run_keeps_its_closing_summary_in_view(copies):
    """The one line that says whether the run passed is never indexed away."""
    agent = small_budget_agent()
    log = pytest_log() * copies
    last_line = log.rstrip("\n").rsplit("\n", 1)[-1]
    original = shell_result(log)

    result = agent._handle_large_tool_result("run_shell_command", original, [], {})

    shown = "".join(s["text"] for s in result["shown"])
    assert last_line in shown, f"{len(log)}-char log lost its summary line"
    assert len(json.dumps(result, ensure_ascii=False)) <= TARGET


def test_shell_output_already_archived_by_the_tool_is_indexed_whole():
    """The shell caps stdout itself; the index still covers every byte of it."""
    agent = small_budget_agent()
    full = pytest_log() * 4
    from gaia.agents.base.artifacts import retain_excerpt

    original = shell_result(retain_excerpt(agent, full, 10000))

    result = agent._handle_large_tool_result("run_shell_command", original, [], {})

    assert result["total_chars"] == len(full)
    assert json.loads(original["stdout"])["artifact"] == result["artifact"]
    for entry in result["index"]:
        assert (
            read_back(agent, result, entry)
            == full[entry["offset"] : entry["offset"] + entry["length"]]
        )


def test_a_100_match_search_is_bounded_and_grouped_by_file():
    agent = small_budget_agent()
    original = search_result()
    assert len(json.dumps(original)) > 40000

    result = agent._handle_large_tool_result(
        "search_file_content", original, [], {"pattern": "gfx90a"}
    )

    assert len(json.dumps(result, ensure_ascii=False)) <= TARGET
    assert result["total_matches"] == 100 and result["archived_format"] == "grep"
    shown = "".join(s["text"] for s in result["shown"])
    assert "/repo/src/pkg_0/module_0.py (9 matches)" in shown
    assert all(": " in e["label"] for e in result["index"])
    text, _ = chunk_index.render_search(original["matches"])
    for entry in result["index"]:
        assert (
            read_back(agent, result, entry)
            == text[entry["offset"] : entry["offset"] + entry["length"]]
        )


def test_structureless_text_keeps_head_and_tail_and_indexes_the_middle():
    agent = small_budget_agent()
    text = "HEAD " + "m" * 30000 + " TAIL"

    result = agent._handle_large_tool_result("fetch_page", text, [], {})

    assert result["head"].startswith("HEAD") and result["tail"].endswith("TAIL")
    (middle,) = result["index"]
    assert middle["offset"] == len(result["head"])
    assert middle["length"] == result["omitted_chars"]
    assert len(json.dumps(result, ensure_ascii=False)) <= TARGET


def test_dropped_records_are_indexed_and_kept_ones_are_not():
    agent = small_budget_agent()
    payload = {
        "messages": [{"id": f"msg-{i:03d}", "body": "b" * 300} for i in range(60)]
    }

    result = agent._handle_large_tool_result("list_messages", payload, [], {})

    kept = len(result["messages"])
    assert 0 < kept < 60
    assert len(json.dumps(result, ensure_ascii=False)) <= TARGET
    reads = "".join(read_back(agent, result, e) for e in result["index"])
    for i in range(kept, 60):
        assert f'"msg-{i:03d}"' in reads, f"dropped msg-{i:03d} is not indexed"
    # Records are indexed in groups of 20; a group the model already has whole
    # is never listed.
    assert '"msg-000"' not in reads or kept < 20


def test_a_read_tool_output_page_is_never_condensed_again():
    agent = small_budget_agent()
    handle = store_for(agent).put('"quoted"\n' * 2000)
    page = store_for(agent).read(handle, 0, 8000)
    assert len(json.dumps(page)) > THRESHOLD

    assert agent._handle_large_tool_result("read_tool_output", page, [], {}) is page
    wire = agent._create_tool_message("read_tool_output", page)["content"][0]["text"]
    assert json.loads(wire) == page


def test_a_result_between_target_and_threshold_reaches_the_model_whole():
    agent = small_budget_agent()
    payload = {"items": [{"id": i, "text": "t" * 90} for i in range(70)]}
    size = len(json.dumps(payload))
    assert TARGET < size <= THRESHOLD

    fitted = agent._handle_large_tool_result("list_items", payload, [], {})
    wire = agent._create_tool_message("list_items", fitted)["content"][0]["text"]

    assert json.loads(wire) == payload, "the backstop cut what the gate let through"


def test_labels_named_by_the_call_arguments_are_shown_first():
    source = python_module()
    chunks = chunk_index.chunk_text(source, "python")
    shown, _ = chunk_index.select(source, chunks, 3000, named=["helper_7"])
    assert any("def helper_7(" in s["text"] for s in shown)


# ---------------------------------------------------------------------------
# The budget: conservative for a remote model, per profile for local hardware
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", [CLOUD_MODEL, "fireworks.deepseek-v4p1-flash"])
def test_a_cloud_model_gets_the_conservative_budget(model):
    agent = make_agent(device=None, model_id=model)
    assert agent._truncation_budget() == truncation_budget(None) == (30000, 20000)


@pytest.mark.parametrize("device", ["npu", "gpu", "cpu"])
def test_local_device_profiles_keep_their_own_budget(device):
    agent = make_agent(device=device)
    assert agent._truncation_budget() == truncation_budget(device)
    assert truncation_budget("npu") == (30000, 20000)
    assert truncation_budget("gpu") == (60000, 40000)


# ---------------------------------------------------------------------------
# Regression guard: the whole mechanism halves what a long task re-sends
# ---------------------------------------------------------------------------


def _resent(results) -> int:
    """Characters re-sent: every step's prompt carries all earlier results."""
    total = resent = 0
    for size in results:
        resent += total
        total += size
    return resent


def test_indexing_halves_the_characters_a_40_step_task_re_sends():
    agent = small_budget_agent()
    large = [
        (
            "read_file",
            {
                "status": "success",
                "file_path": "/r/a.py",
                "file_type": "python",
                "content": python_module(),
            },
        ),
        ("search_file_content", search_result()),
        ("run_shell_command", shell_result(pytest_log() * 4)),
        (
            "read_file",
            {
                "status": "success",
                "file_path": "/r/README.md",
                "file_type": "markdown",
                "content": MARKDOWN * 40,
            },
        ),
    ] * 3
    rng = random.Random(7)
    steps = ["small"] * 28 + list(range(12))
    rng.shuffle(steps)
    raw, condensed = [], []
    for step in steps:
        if step == "small":
            result = {"status": "success", "stdout": "ok\n" * 150}
            name = "run_shell_command"
        else:
            name, result = large[step]
        raw.append(len(json.dumps(result, ensure_ascii=False)))
        fitted = agent._handle_large_tool_result(name, result, [], {})
        condensed.append(len(json.dumps(fitted, ensure_ascii=False)))
    assert len(raw) == 40 and sum(r > THRESHOLD for r in raw) == 12

    before, after = _resent(raw), _resent(condensed)
    assert after <= before * 0.5, (
        f"re-sent {after:,} chars with the index vs {before:,} raw -- "
        f"only {100 * (before - after) / before:.0f}% saved"
    )


def test_artifact_store_exposes_whole_text_only_for_live_handles():
    store = ArtifactStore()
    handle = store.put("abc")
    assert store.has(handle) and store.text(handle) == "abc"
    assert not store.has("output_missing") and not store.has(None)
    with pytest.raises(ValueError, match="Unknown"):
        store.text("output_missing")


def test_condense_result_raises_rather_than_exceed_its_target(monkeypatch):
    """A bug that overshoots must surface, not ship an oversized prompt."""
    monkeypatch.setattr(
        chunk_index, "select", lambda *a, **k: ([{"offset": 0, "text": "x" * 9000}], [])
    )
    with pytest.raises(ValueError, match="over its"):
        chunk_index.condense_result(
            "read_file",
            {"file_path": "/a.py", "file_type": "python", "content": python_module()},
            {},
            TARGET,
            ArtifactStore(),
            json.dumps,
        )


# ---------------------------------------------------------------------------
# Any index entry reads back whole, however long
# ---------------------------------------------------------------------------


def read_entry(agent, result, entry):
    """Read an entry by its number, following ``next_offset`` while it pages."""
    reader = agent._tools_registry["read_tool_output"]["function"]
    page = reader(result["artifact"], entry=entry["n"])
    assert page["entry"] == entry["n"] and page["offset"] == entry["offset"]
    parts = [page["content"]]
    while "remaining" in page:
        assert len(page["content"]) == 8000
        page = reader(result["artifact"], page["next_offset"], page["remaining"])
        parts.append(page["content"])
    text = "".join(parts)
    assert len(text) == entry["length"]
    return text


def _assert_entries_read_back(agent, result, text):
    assert len(json.dumps(result, ensure_ascii=False)) <= TARGET
    for entry in result["index"]:
        span = text[entry["offset"] : entry["offset"] + entry["length"]]
        assert read_entry(agent, result, entry) == span
    for segment in result["shown"]:
        start = segment["offset"]
        assert text[start : start + len(segment["text"])] == segment["text"]


@pytest.mark.parametrize(
    "classes,size", [(24, 100_000), (240, 1_000_000)], ids=["100KB", "1MB"]
)
def test_every_entry_of_a_huge_python_file_reads_back(classes, size):
    source = python_module(n_classes=classes, methods=6, functions=10)
    assert len(source) > size
    agent = small_budget_agent()
    original = {"status": "success", "file_path": "/r/big.py", "file_type": "python"}
    original["content"] = source

    result = agent._handle_large_tool_result("read_file", original, [], {})

    if size == 1_000_000:
        assert any(e["length"] > 8000 for e in result["index"])
    _assert_entries_read_back(agent, result, source)


def test_a_single_50k_line_keeps_the_end_of_the_output_in_view():
    agent = small_budget_agent()
    stdout = "header\n\n" + "x" * 50000 + "END-OF-RUN\n"
    original = shell_result(stdout)

    result = agent._handle_large_tool_result("run_shell_command", original, [], {})

    shown = "".join(s["text"] for s in result["shown"])
    assert shown.startswith("header") and "END-OF-RUN" in shown
    assert result["check_result"] == CHECK
    (middle,) = result["index"]
    assert middle["length"] > 8000
    _assert_entries_read_back(agent, result, stdout)


def test_the_head_tail_middle_of_structureless_text_reads_back_whole():
    agent = small_budget_agent()
    text = "HEAD " + "m" * 60000 + " TAIL"
    result = agent._handle_large_tool_result("fetch_page", text, [], {})
    (middle,) = result["index"]
    assert (
        read_entry(agent, result, middle)
        == text[middle["offset"] : middle["offset"] + middle["length"]]
    )


# ---------------------------------------------------------------------------
# Search matches with missing fields
# ---------------------------------------------------------------------------


def test_search_matches_without_a_file_or_with_a_text_context_render():
    matches = [
        {"file": "", "line": 1, "content": "a"},
        {"line": 2, "content": "b"},
        {"file": "/r/x.py", "line": 3, "content": "c", "context": "one line"},
        {"file": "/r/y.py", "line": 4, "content": "d", "context": ["p", "q"]},
    ]
    text, chunks = chunk_index.render_search(matches)
    assert_tiles(text, chunks)
    assert text.startswith("(no file) (2 matches)\n")
    assert chunks[0].label.startswith("(no file): 2 matches")
    assert "    | one line\n" in text and "    | o\n" not in text
    assert "    | p\n    | q\n" in text


# ---------------------------------------------------------------------------
# A local model served by Lemonade keeps its device budget
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["npu", "gpu"])
def test_a_lemonade_served_local_model_keeps_the_device_budget(monkeypatch, profile):
    from gaia.config import GaiaConfig
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    monkeypatch.setattr(GaiaConfig, "load", lambda: GaiaConfig(default_device=profile))
    agent = make_agent(device=None, model_id=DEFAULT_MODEL_NAME)

    assert agent._truncation_budget() == truncation_budget(profile)


# ---------------------------------------------------------------------------
# Property: mixed text tiles exactly and condenses within the target
# ---------------------------------------------------------------------------

_MIXED = [
    "",
    "plain ünïcode — λ 漢字 🚀",
    "# Heading",
    "### Deep heading",
    "```python",
    "```",
    "def f(x):",
    "    return x",
    "class K:",
    "==== FAILURES ====",
    "___ test_it ___",
    "FAILED t.py::a - boom",
    "Traceback (most recent call last):",
    "src/a.py:3: hit",
    "diff --git a/x b/x",
    "@@ -1 +1 @@",
    '{"k": [1, 2]}',
    "x" * 900,
]


def _mixed_text(rng: random.Random) -> str:
    out = []
    for _ in range(rng.randint(20, 400)):
        out.append(rng.choice(_MIXED))
        out.append(rng.choice(["\n", "\n", "\n", "\r\n", "\r"]))
    return "".join(out)


@pytest.mark.parametrize("seed", range(30))
def test_mixed_text_tiles_and_condenses_within_the_target(seed):
    rng = random.Random(1000 + seed)
    text = _mixed_text(rng)
    for chunker in (
        chunk_index.chunk_markdown,
        chunk_index.chunk_output,
        chunk_index.chunk_diff,
        chunk_index.chunk_paragraphs,
        chunk_index.chunk_lines,
    ):
        chunks = chunker(text)
        if chunks:
            assert_tiles(text, chunks)
    for kind in ("python", "markdown", "json", "output", "diff", "text"):
        chunks = chunk_index.chunk_text(text, kind)
        if chunks:
            assert_tiles(text, chunks)

    store = ArtifactStore()
    shapes = [
        ("fetch_page", text),
        ("run_shell_command", shell_result(text)),
        (
            "read_file",
            {"file_path": "/r/a.md", "file_type": "markdown", "content": text},
        ),
        ("read_file", {"file_path": "/r/a.py", "file_type": "python", "content": text}),
    ]
    for name, result in shapes:
        condensed = chunk_index.condense_result(
            name, result, {}, TARGET, store, lambda v: json.dumps(v, ensure_ascii=False)
        )
        if condensed is None:
            continue
        assert len(json.dumps(condensed, ensure_ascii=False)) <= TARGET
        archived = store.text(condensed["artifact"])
        assert archived == text
        for segment in condensed["shown"]:
            start = segment["offset"]
            assert archived[start : start + len(segment["text"])] == segment["text"]
        assert condensed["fetch"] == chunk_index.FETCH_HINT
        assert [e["n"] for e in condensed["index"]] == list(
            range(1, len(condensed["index"]) + 1)
        )
        for entry in condensed["index"]:
            span = archived[entry["offset"] : entry["offset"] + entry["length"]]
            page = store.read(condensed["artifact"], entry=entry["n"])
            assert page["content"] == span[:8000]


# ---------------------------------------------------------------------------
# Entries are read by number, on every path that condenses
# ---------------------------------------------------------------------------


def _metadata(result):
    """The dict carrying artifact/index: the result, or a list's last record."""
    if isinstance(result, str):
        result = json.loads(result)
    return result[-1] if isinstance(result, list) else result


_PATHS = {
    "python": (
        "read_file",
        lambda: {
            "file_path": "/r/a.py",
            "file_type": "python",
            "content": python_module(),
        },
    ),
    "markdown": (
        "read_file",
        lambda: {
            "file_path": "/r/a.md",
            "file_type": "markdown",
            "content": MARKDOWN * 60,
        },
    ),
    "json": (
        "read_file",
        lambda: {
            "file_path": "/r/a.json",
            "content": json.dumps(
                {f"k{i}": list(range(60)) for i in range(40)}, indent=2
            ),
        },
    ),
    "output": ("run_shell_command", lambda: shell_result(pytest_log() * 4)),
    "diff": (
        "edit_file",
        lambda: {
            "status": "success",
            "file_path": "/r/a.py",
            "diff": "".join(
                f"--- a/f{i}.py\n+++ b/f{i}.py\n@@ -1,40 +1,40 @@\n"
                + "".join(f"-old {k}\n+new {k}\n" for k in range(40))
                for i in range(12)
            ),
        },
    ),
    "text": (
        "fetch_page",
        lambda: "\n\n".join(f"Paragraph {i}. " + "words " * 80 for i in range(60)),
    ),
    "search": ("search_file_content", search_result),
    "head/tail": (
        "run_shell_command",
        lambda: shell_result("header\n\n" + "x" * 50000 + "END\n"),
    ),
    "elide": ("fetch_page", lambda: "HEAD " + "m" * 40000 + " TAIL"),
    "records": (
        "list_messages",
        lambda: {
            "messages": [{"id": f"msg-{i:03d}", "body": "b" * 300} for i in range(80)]
        },
    ),
}


@pytest.mark.parametrize("path", list(_PATHS))
def test_every_index_entry_reads_back_by_number(path):
    name, build = _PATHS[path]
    agent = small_budget_agent()
    result = agent._handle_large_tool_result(name, build(), [], {})
    meta = _metadata(result)
    assert meta["fetch"] == chunk_index.FETCH_HINT
    archived = store_for(agent).text(meta["artifact"])
    assert meta["index"], "nothing was indexed"
    assert [e["n"] for e in meta["index"]] == list(range(1, len(meta["index"]) + 1))
    for entry in meta["index"]:
        span = archived[entry["offset"] : entry["offset"] + entry["length"]]
        assert read_entry(agent, meta, entry) == span
    assert len(json.dumps(result, ensure_ascii=False)) <= TARGET


def test_a_bad_entry_number_says_what_to_do():
    agent = small_budget_agent()
    result = agent._handle_large_tool_result(
        *_PATHS["python"][:1], _PATHS["python"][1](), [], {}
    )
    reader = agent._tools_registry["read_tool_output"]["function"]
    count = len(result["index"])
    for bad in (0, count + 1):
        with pytest.raises(ValueError, match=f"lists entries 1-{count}"):
            reader(result["artifact"], entry=bad)
    with pytest.raises(ValueError, match="entry number"):
        reader(result["artifact"], entry="2")
    with pytest.raises(ValueError, match="not both"):
        reader(result["artifact"], 5, entry=1)
    from gaia.agents.base.artifacts import retain_excerpt

    excerpt = json.loads(retain_excerpt(agent, "y" * 20000, 2000))
    with pytest.raises(ValueError, match="has no index; read it with offset"):
        reader(excerpt["artifact"], entry=1)


def test_the_reader_advertises_entry_first_and_no_fixed_page_size():
    agent = small_budget_agent()
    agent._register_output_reader()
    from gaia.agents.base.tools import (
        MAX_TOOL_DESCRIPTION_CHARS,
        MAX_TOOL_PARAM_DESCRIPTION_CHARS,
    )

    doc = agent._tools_registry["read_tool_output"]["description"]
    assert "1 to 8000" not in doc
    assert "almost never needed" in doc
    params = agent._tools_registry["read_tool_output"]["parameters"]
    assert list(params) == ["artifact", "entry", "offset", "limit"]
    assert params["entry"]["type"] == "integer" and not params["entry"]["required"]
    # Re-sent on every call, so it answers to the same budget as every tool.
    assert len(doc) <= MAX_TOOL_DESCRIPTION_CHARS
    assert all(
        len(p.get("description", "")) <= MAX_TOOL_PARAM_DESCRIPTION_CHARS
        for p in params.values()
    )
