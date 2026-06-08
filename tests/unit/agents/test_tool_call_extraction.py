# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for _extract_embedded_tool_call — resilient fenced-JSON extraction.

Stage A of issue #1428: verify the base parser's decision logic:
  1. ≥1 unfenced candidate → return the first (unchanged behaviour)
  2. exactly one fenced candidate → return it (the fix)
  3. >1 fenced + 0 unfenced → ambiguous → None + warning
  4. no candidates → None
"""

import logging
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent

# ---------------------------------------------------------------------------
# Minimal Agent subclass that avoids LLM/registry/console init
# ---------------------------------------------------------------------------


class _MinimalAgent(Agent):
    """Minimal Agent subclass that avoids LLM/registry/console init."""

    def _register_tools(self):
        pass


@pytest.fixture()
def parser():
    """Return a minimal Agent instance whose _extract_embedded_tool_call we can call."""
    with patch.object(_MinimalAgent, "__init__", return_value=None):
        agent = _MinimalAgent.__new__(_MinimalAgent)
    # Provide the bare minimum: snapshot used by _tools_registry property
    agent._tool_snapshot = {}
    return agent


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _call(parser, text):
    return parser._extract_embedded_tool_call(text)


# ---------------------------------------------------------------------------
# 1. Unfenced candidates — existing behaviour must be preserved
# ---------------------------------------------------------------------------


class TestUnfencedCandidates:
    def test_bare_call_extracted(self, parser):
        response = '{"tool": "create_agent", "tool_args": {"name": "Foo"}}'
        result = _call(parser, response)
        assert result is not None
        assert result["tool"] == "create_agent"

    def test_narration_then_bare_json(self, parser):
        response = (
            "Sure, creating it now.\n"
            '{"tool": "create_agent", "tool_args": {"name": "Foo"}}'
        )
        result = _call(parser, response)
        assert result is not None
        assert result["tool"] == "create_agent"

    def test_fenced_example_plus_real_unfenced_wins(self, parser):
        """Unfenced call must win even when a fenced call is also present."""
        response = (
            "Here is an example:\n"
            "```json\n"
            '{"tool": "example_tool", "tool_args": {}}\n'
            "```\n"
            "Now the real call:\n"
            '{"tool": "create_agent", "tool_args": {"name": "Real"}}'
        )
        result = _call(parser, response)
        assert result is not None
        assert result["tool"] == "create_agent"

    def test_plain_text_no_tool_key_returns_none(self, parser):
        result = _call(parser, "Just some text with no JSON at all.")
        assert result is None

    def test_no_tool_key_in_json_returns_none(self, parser):
        result = _call(parser, '{"thought": "hmm", "answer": "okay"}')
        assert result is None


# ---------------------------------------------------------------------------
# 2. Single fenced candidate — the fix for #1428
# ---------------------------------------------------------------------------


class TestSingleFencedCandidate:
    # Verbatim Zephyr regression fixture — must be extracted
    ZEPHYR_RESPONSE = (
        "Creating your Zephyr Agent now! 🎉\n"
        "\n"
        "```json\n"
        '{"tool": "create_agent", "tool_args": {"name": "Zephyr Agent", "description": "A versatile agent"}}\n'
        "```\n"
        "\n"
        "✅ **Agent Created!**\n"
        "File location: `~/.gaia/agents/Zephyr Agent/agent.py`"
    )

    def test_zephyr_regression_fixture(self, parser):
        result = _call(parser, self.ZEPHYR_RESPONSE)
        assert result is not None, "Zephyr capture must be extracted from fenced block"
        assert result["tool"] == "create_agent"
        assert result["tool_args"]["name"] == "Zephyr Agent"

    def test_backtick_json_fence(self, parser):
        response = (
            "Some narrative.\n"
            "```json\n"
            '{"tool": "do_thing", "tool_args": {"x": 1}}\n'
            "```"
        )
        result = _call(parser, response)
        assert result is not None
        assert result["tool"] == "do_thing"

    def test_bare_backtick_fence(self, parser):
        """Bare ``` fence (no language tag) must also be detected."""
        response = (
            "Here you go:\n" "```\n" '{"tool": "do_thing", "tool_args": {}}\n' "```"
        )
        result = _call(parser, response)
        assert result is not None
        assert result["tool"] == "do_thing"

    def test_fence_at_start_of_response(self, parser):
        response = '```json\n{"tool": "start_tool", "tool_args": {}}\n```'
        result = _call(parser, response)
        assert result is not None
        assert result["tool"] == "start_tool"

    def test_unclosed_fence_still_extracted(self, parser):
        """An unclosed fence should not crash; the JSON inside is still valid."""
        response = (
            "Starting now:\n" "```json\n" '{"tool": "open_tool", "tool_args": {}}\n'
        )
        result = _call(parser, response)
        assert result is not None
        assert result["tool"] == "open_tool"

    def test_tool_args_auto_added_when_missing(self, parser):
        response = '```json\n{"tool": "simple"}\n```'
        result = _call(parser, response)
        assert result is not None
        assert result["tool"] == "simple"
        assert result["tool_args"] == {}


# ---------------------------------------------------------------------------
# 3. Multiple fenced candidates → ambiguous → None + warning
# ---------------------------------------------------------------------------


class TestMultipleFencedCandidates:
    def test_two_fenced_returns_none(self, parser, caplog):
        response = (
            "Option A:\n"
            "```json\n"
            '{"tool": "tool_a", "tool_args": {}}\n'
            "```\n"
            "Option B:\n"
            "```json\n"
            '{"tool": "tool_b", "tool_args": {}}\n'
            "```"
        )
        with caplog.at_level(logging.WARNING):
            result = _call(parser, response)
        assert result is None
        assert any("ambiguous" in r.message.lower() for r in caplog.records)

    def test_three_fenced_returns_none(self, parser):
        fenced = '```json\n{"tool": "t", "tool_args": {}}\n```'
        response = f"{fenced}\n{fenced}\n{fenced}"
        result = _call(parser, response)
        assert result is None


# ---------------------------------------------------------------------------
# 4. Robustness — no crash on malformed / edge-case inputs
# ---------------------------------------------------------------------------


class TestRobustness:
    def test_malformed_json_in_fence_returns_none(self, parser):
        """Malformed JSON inside a fence must not crash — return None."""
        response = '```json\n{"tool": "oops", bad json}\n```'
        result = _call(parser, response)
        assert result is None

    def test_nested_braces_in_string_value(self, parser):
        response = (
            "```json\n"
            '{"tool": "t", "tool_args": {"query": "find {braces} here"}}\n'
            "```"
        )
        result = _call(parser, response)
        assert result is not None
        assert result["tool_args"]["query"] == "find {braces} here"

    def test_trailing_comma_repaired(self, parser):
        response = "```json\n" '{"tool": "t", "tool_args": {"a": 1,}}\n' "```"
        result = _call(parser, response)
        assert result is not None
        assert result["tool"] == "t"

    def test_quotes_inside_string_value(self, parser):
        response = (
            "```json\n" '{"tool": "t", "tool_args": {"msg": "say \\"hello\\""}}\n' "```"
        )
        result = _call(parser, response)
        assert result is not None

    def test_empty_response_returns_none(self, parser):
        assert _call(parser, "") is None

    def test_no_tool_key_in_fenced_json_returns_none(self, parser):
        """Fenced JSON that has no 'tool' key must not be returned."""
        response = '```json\n{"action": "foo", "data": 1}\n```'
        result = _call(parser, response)
        assert result is None

    def test_native_sentinel_not_touched(self, parser):
        """The __tool_calls__ sentinel is handled in _parse_llm_response,
        not _extract_embedded_tool_call — the method just returns None for it
        since it does not contain an unfenced/fenced {tool:...} block."""
        # The sentinel typically starts with '{"__tool_calls__":'
        # _extract_embedded_tool_call scans for "tool" — the sentinel has "tool" in
        # "__tool_calls__". It is NOT a valid tool-call dict (no top-level "tool" key).
        response = '{"__tool_calls__": [{"name": "foo", "arguments": "{}"}]}'
        result = _call(parser, response)
        # Should not interpret the envelope as a tool call (no top-level "tool" key)
        assert result is None
