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


def test_loop_break_summary_never_claims_completion():
    """No string literal in ``_build_loop_break_summary`` may say "Task
    completed". The helper only runs after the loop guard STOPPED a turn, so
    any completion claim from it is false — first as duplicated literals (the
    lie-on-loop bug), then as the non-error branch (#3750).

    The walk is scoped to the helper's body so unrelated mentions
    (assertions in tests, future docstrings, comments) don't trip
    this invariant.
    """
    src = AGENT_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    helper = _find_function(tree, "_build_loop_break_summary")
    hits = [n for n in _string_literals_in(helper) if "Task completed" in n.value]
    assert not hits, (
        f"_build_loop_break_summary claims completion at lines "
        f"{[n.lineno for n in hits]} — a loop break is never a finish"
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
