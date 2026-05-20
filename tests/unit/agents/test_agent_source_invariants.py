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


def _string_literals(tree: ast.AST):
    """Yield every ``ast.Constant`` whose value is a ``str``.

    Captures plain strings, f-strings' constant parts, and docstrings.
    For f-strings we walk the JoinedStr.values and yield the Constant
    parts (which include the literal text between ``{...}`` slots).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node


def test_task_completed_with_appears_in_exactly_one_string_literal():
    """``"Task completed with"`` must live in exactly ONE string
    literal in the file — the f-string return value inside
    ``_build_loop_break_summary``. Two copies (one per legacy
    loop-break site) was the lie-on-loop bug; rewording the docstring
    is the maintainer's lever to keep this count at 1."""
    src = AGENT_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    hits = [n for n in _string_literals(tree) if "Task completed with" in n.value]
    assert len(hits) == 1, (
        f"expected exactly 1 'Task completed with' literal in agent.py, "
        f"found {len(hits)} at lines {[n.lineno for n in hits]}"
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
