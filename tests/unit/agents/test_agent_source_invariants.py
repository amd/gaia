# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Static-source invariants on ``gaia/agents/base/agent.py``.

These tests parse the source and assert structural properties that
guard against regressions which unit-level mocks can't catch. They run
in milliseconds and don't import the module.
"""

import ast
from pathlib import Path

AGENT_PY = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "gaia"
    / "agents"
    / "base"
    / "agent.py"
)


def _string_literals_in(node: ast.AST):
    """Yield every ``str`` ``ast.Constant`` under ``node``.

    Captures plain strings, f-strings' constant parts, and docstrings.
    """
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n  # type: ignore[return-value]
    raise AssertionError(f"function {name!r} not found in agent.py")


def test_task_completed_with_appears_in_exactly_one_string_literal():
    """``"Task completed with"`` must live in exactly ONE string
    literal inside ``_build_loop_break_summary``. Two copies (one per
    legacy loop-break site) was the lie-on-loop bug.

    The walk is scoped to the helper's body so unrelated mentions
    (assertions in tests, future docstrings, comments) don't trip
    this invariant.
    """
    src = AGENT_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    helper = _find_function(tree, "_build_loop_break_summary")
    hits = [n for n in _string_literals_in(helper) if "Task completed with" in n.value]
    assert len(hits) == 1, (
        f"expected exactly 1 'Task completed with' literal in "
        f"_build_loop_break_summary, found {len(hits)} at "
        f"lines {[n.lineno for n in hits]}"
    )


def test_build_loop_break_summary_helper_exists():
    """Sanity: the helper method that owns the literal must exist."""
    src = AGENT_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    method_names = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_build_loop_break_summary" in method_names
