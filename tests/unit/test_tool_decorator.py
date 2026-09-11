# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for the @tool decorator functionality.

Purpose: Verify that the tool decorator correctly registers tools
with all metadata including the atomic parameter.
"""

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY, tool


class TestToolDecorator:
    """Test suite for the @tool decorator."""

    @pytest.fixture(autouse=True)
    def clear_registry(self):
        """Clear the tool registry before and after each test."""
        _TOOL_REGISTRY.clear()
        yield
        _TOOL_REGISTRY.clear()

    def test_tool_registration_basic(self):
        """Test that a basic tool is registered correctly."""

        @tool
        def my_tool(param: str) -> dict:
            """A test tool."""
            return {"result": param}

        assert "my_tool" in _TOOL_REGISTRY
        assert _TOOL_REGISTRY["my_tool"]["name"] == "my_tool"
        assert _TOOL_REGISTRY["my_tool"]["description"] == "A test tool."
        assert callable(_TOOL_REGISTRY["my_tool"]["function"])

    def test_tool_atomic_default_false(self):
        """Test that atomic defaults to False when not specified."""

        @tool
        def regular_tool() -> str:
            """A regular tool."""
            return "result"

        assert "regular_tool" in _TOOL_REGISTRY
        assert _TOOL_REGISTRY["regular_tool"]["atomic"] is False

    def test_tool_atomic_true(self):
        """Test that atomic=True is correctly stored in registry."""

        @tool(atomic=True)
        def atomic_tool() -> str:
            """An atomic tool."""
            return "result"

        assert "atomic_tool" in _TOOL_REGISTRY
        assert _TOOL_REGISTRY["atomic_tool"]["atomic"] is True

    def test_tool_atomic_false_explicit(self):
        """Test that atomic=False can be explicitly set."""

        @tool(atomic=False)
        def explicit_non_atomic() -> str:
            """A non-atomic tool."""
            return "result"

        assert "explicit_non_atomic" in _TOOL_REGISTRY
        assert _TOOL_REGISTRY["explicit_non_atomic"]["atomic"] is False

    def test_supported_kwargs_are_registered_unchanged(self):
        """All supported decorator kwargs retain their registry values."""

        @tool(atomic=True, display_label="Run report", timeout=42.5)
        def configured_tool() -> str:
            """A configured tool."""
            return "result"

        metadata = _TOOL_REGISTRY["configured_tool"]
        assert metadata["atomic"] is True
        assert metadata["display_label"] == "Run report"
        assert metadata["timeout"] == 42.5

    def test_multiple_tools_mixed_atomic(self):
        """Test that multiple tools can have different atomic values."""

        @tool(atomic=True)
        def tool_a() -> str:
            """Atomic tool A."""
            return "a"

        @tool
        def tool_b() -> str:
            """Regular tool B."""
            return "b"

        @tool(atomic=True)
        def tool_c() -> str:
            """Atomic tool C."""
            return "c"

        assert _TOOL_REGISTRY["tool_a"]["atomic"] is True
        assert _TOOL_REGISTRY["tool_b"]["atomic"] is False
        assert _TOOL_REGISTRY["tool_c"]["atomic"] is True

    def test_tool_parameters_captured(self):
        """Test that tool parameters are correctly captured."""

        @tool(atomic=True)
        def param_tool(name: str, count: int, enabled: bool = True) -> dict:
            """A tool with various parameters."""
            return {"name": name, "count": count, "enabled": enabled}

        params = _TOOL_REGISTRY["param_tool"]["parameters"]
        assert "name" in params
        assert params["name"]["type"] == "string"
        assert params["name"]["required"] is True

        assert "count" in params
        assert params["count"]["type"] == "integer"
        assert params["count"]["required"] is True

        assert "enabled" in params
        assert params["enabled"]["type"] == "boolean"
        assert params["enabled"]["required"] is False

    def test_tool_function_callable(self):
        """Test that the registered function is callable and works."""

        @tool(atomic=True)
        def working_tool(value: str) -> dict:
            """A working tool."""
            return {"processed": value.upper()}

        func = _TOOL_REGISTRY["working_tool"]["function"]
        result = func(value="hello")
        assert result == {"processed": "HELLO"}

    def test_tool_empty_parentheses_registers(self):
        """The explicit empty-parentheses form remains supported."""

        @tool()
        def empty_parentheses_tool() -> str:
            return "result"

        assert "empty_parentheses_tool" in _TOOL_REGISTRY

    @pytest.mark.parametrize("unexpected", ["risk_tier", "atmoic", "timeuot"])
    def test_unexpected_kwargs_fail_at_decoration(self, unexpected):
        """Unknown and misspelled kwargs fail before registry insertion."""

        def demo_tool() -> str:
            return "result"

        with pytest.raises(TypeError) as exc_info:
            tool(**{unexpected: True})(demo_tool)

        assert str(exc_info.value) == (
            f"@tool(...) got unexpected keyword argument '{unexpected}' for tool "
            "'demo_tool'. Accepted: atomic, display_label, timeout."
        )
        assert "demo_tool" not in _TOOL_REGISTRY
