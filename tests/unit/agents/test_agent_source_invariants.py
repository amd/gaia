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


def test_no_loop_break_path_claims_the_task_was_completed():
    """Neither branch of the loop-break summary may report success.

    The original bug was two copies of ``"Task completed with"``, one per
    legacy loop-break site, so this asserted there was exactly one. The
    remaining copy turned out to be a lie too: a loop of *succeeding* calls
    that never reach the goal ended the turn claiming the task was done.
    The invariant is now zero, not one.

    Scoped to the helper's body so a docstring elsewhere can still describe
    the historical wording without tripping this.
    """
    src = AGENT_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    helper = _find_function(tree, "_build_loop_break_summary")
    body = [n for n in _string_literals_in(helper) if not _is_docstring(helper, n)]
    hits = [n for n in body if "Task completed" in n.value]
    assert not hits, (
        "the loop-break summary must never claim completion; found "
        f"{len(hits)} such literal(s) at lines {[n.lineno for n in hits]}"
    )


def _is_docstring(fn: ast.FunctionDef, node: ast.Constant) -> bool:
    """True when *node* is *fn*'s own docstring."""
    first = fn.body[0] if fn.body else None
    return (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and first.value is node
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
