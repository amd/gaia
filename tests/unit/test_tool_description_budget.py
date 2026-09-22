# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the tool-description budget (util/check_tool_descriptions.py).

A tool's docstring is its schema, and every model call re-sends the whole
schema set — so the budget is what keeps one verbose docstring from being
billed on every step of every turn.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

from check_tool_descriptions import find_violations  # noqa: E402

from gaia.agents.base.tools import (  # noqa: E402
    _TOOL_REGISTRY,
    MAX_TOOL_DESCRIPTION_CHARS,
    MAX_TOOL_PARAM_DESCRIPTION_CHARS,
    _schema_description,
    tool,
)


def _schema(name: str, description: str, params: dict | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": params or {}},
        },
    }


def _violations(schemas: list) -> list:
    return find_violations(
        schemas, MAX_TOOL_DESCRIPTION_CHARS, MAX_TOOL_PARAM_DESCRIPTION_CHARS
    )


class TestDescriptionBudget:
    def test_over_budget_description_is_reported_with_the_tool_name(self):
        over = "x" * (MAX_TOOL_DESCRIPTION_CHARS + 1)
        messages = _violations([_schema("chatty_tool", over)])

        assert len(messages) == 1
        assert "chatty_tool" in messages[0]
        assert str(MAX_TOOL_DESCRIPTION_CHARS + 1) in messages[0]
        assert str(MAX_TOOL_DESCRIPTION_CHARS) in messages[0]

    def test_description_at_the_budget_passes(self):
        at_budget = "x" * MAX_TOOL_DESCRIPTION_CHARS
        assert _violations([_schema("terse_tool", at_budget)]) == []

    def test_over_budget_parameter_is_reported_with_tool_and_parameter(self):
        params = {
            "haystack": {
                "type": "string",
                "description": "y" * (MAX_TOOL_PARAM_DESCRIPTION_CHARS + 1),
            }
        }
        messages = _violations([_schema("search_thing", "Find a thing.", params)])

        assert len(messages) == 1
        assert "search_thing(haystack)" in messages[0]
        assert str(MAX_TOOL_PARAM_DESCRIPTION_CHARS) in messages[0]

    def test_parameter_at_the_budget_passes(self):
        params = {
            "haystack": {
                "type": "string",
                "description": "y" * MAX_TOOL_PARAM_DESCRIPTION_CHARS,
            }
        }
        assert _violations([_schema("search_thing", "Find a thing.", params)]) == []


class TestSchemaDescription:
    """What the registry ships: the docstring, cleaned, without ``Args:``."""

    def test_args_block_is_dropped_but_later_sections_stay(self):
        docstring = (
            "Do the thing.\n\n"
            "Args:\n    path: Where to do it.\n\n"
            "Returns:\n    Status and path.\n"
        )

        assert (
            _schema_description(docstring)
            == "Do the thing.\n\nReturns:\n    Status and path."
        )

    def test_prose_after_the_args_block_survives(self):
        """Dedenting out of ``Args:`` ends it, even with no ``Returns:`` header.

        The argument parser already stops at that dedent, so anything the
        schema dropped here would be text no consumer ever sees.
        """
        docstring = (
            "Do the thing.\n\n"
            "Args:\n    path: Where to do it.\n\n"
            "Refuses to touch anything outside the workspace.\n"
        )

        assert _schema_description(docstring) == (
            "Do the thing.\n\nRefuses to touch anything outside the workspace."
        )

    def test_argument_continuation_lines_are_still_dropped(self):
        """Indented continuations belong to the argument, not the prose."""
        docstring = (
            "Do the thing.\n\n"
            "Args:\n"
            "    path: Where to do it.\n"
            "        Must already exist.\n"
        )

        assert _schema_description(docstring) == "Do the thing."

    def test_leading_indentation_is_stripped(self):
        docstring = "Do the thing.\n\n            Second line of prose.\n"

        assert "    " not in _schema_description(docstring)

    @pytest.fixture
    def registered(self):
        @tool
        def budgeted_example(path: str) -> dict:
            """Do the thing.

            Args:
                path: Where to do it.
            """
            return {"path": path}

        yield _TOOL_REGISTRY["budgeted_example"]
        _TOOL_REGISTRY.pop("budgeted_example", None)

    def test_registered_tool_splits_prose_from_argument_text(self, registered):
        assert registered["description"] == "Do the thing."
        assert registered["parameters"]["path"]["description"] == "Where to do it."
