# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What two tool descriptions tell the model about when NOT to use them.

``get_shell_state`` was called to run a command the user asked for, and
``remember_skill_lesson`` to save the user's own content. The description is
the only place the model learns the boundary, so it is pinned here, inside
the per-tool description budget.
"""

from __future__ import annotations

import pytest

from gaia.agents.base.tools import (
    _TOOL_REGISTRY,
    MAX_TOOL_DESCRIPTION_CHARS,
    TOOL_DESCRIPTION_ALLOWANCES,
)
from gaia.agents.tools.shell_tools import ShellToolsMixin
from gaia.agents.tools.skill_learning_tools import SkillLearningToolsMixin


class _ShellHost(ShellToolsMixin):
    pass


@pytest.fixture
def registry():
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        _ShellHost().register_shell_tools()
        SkillLearningToolsMixin.register_skill_learning_tools(
            object.__new__(SkillLearningToolsMixin)
        )
        yield _TOOL_REGISTRY
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _flat(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize("name", ["get_shell_state", "remember_skill_lesson"])
def test_descriptions_fit_their_budget(registry, name):
    budget = TOOL_DESCRIPTION_ALLOWANCES.get(name, MAX_TOOL_DESCRIPTION_CHARS)
    assert len(registry[name]["description"]) <= budget


def test_get_shell_state_says_it_reads_state_and_runs_nothing(registry):
    text = _flat(registry["get_shell_state"]["description"])

    assert text.startswith("Read the shell session's state")
    assert "working directory" in text
    assert "runs no command" in text
    assert "To run a command the user asked for" in text
    assert "`run_shell_command`" in text


def test_get_shell_state_does_not_promise_environment_variables(registry):
    """It returns only the directory; claiming more would route env reads here."""
    text = _flat(registry["get_shell_state"]["description"])
    result = registry["get_shell_state"]["function"]()

    assert "reports no environment variables" in text
    assert set(result) == {"status", "cwd", "has_errors"}


def test_remember_skill_lesson_excludes_the_users_own_data(registry):
    text = _flat(registry["remember_skill_lesson"]["description"])

    assert "how a skill should run" in text
    assert "never their own data or content" in text
    # The earlier exclusions survive the rewrite.
    for kept in ("page or output text", "task notes", "one failure", "`remember`"):
        assert kept in text, kept
