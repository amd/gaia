# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for ``Agent._dedup_mutation_call`` (issue #1317).

Query-family tools had result-level dedup, but mutation tools (mark_read,
archive_message, …) had none — a small model that re-issued an identical
mutation only got caught by the slow reactive loop-detector after ~4 wasted
steps. The helper catches an identical mutation re-issue at the FIRST repeat,
keyed on ``(tool, normalized args)`` so mutations on *different* ids are never
suppressed.

These tests exercise the helper directly so they're deterministic — no live
Lemonade, no LLM sampling.
"""

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent


class _DummyAgent(Agent):
    """Minimal concrete Agent — copied pattern from test_loop_break_truthful."""

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        return _DummyAgent(silent_mode=True, skip_lemonade=True)


# ---------------------------------------------------------------------------
# First-repeat detection (acceptance criterion 1 + 2)
# ---------------------------------------------------------------------------


def test_identical_mutation_caught_at_first_repeat(agent):
    """Same tool + same id on the 2nd call → corrective signal injected."""
    cache: dict[str, int] = {}
    messages: list[dict] = []
    args = {"message_id": "abc123"}

    # First call: no signal — the mutation is legitimate.
    agent._dedup_mutation_call("mark_read", args, cache, messages)
    assert messages == []

    # Second identical call: caught immediately (parity with query dedup).
    agent._dedup_mutation_call("mark_read", args, cache, messages)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "mark_read" in messages[0]["content"]
    # A re-plan signal, not a silent drop.
    assert "[SYSTEM]" in messages[0]["content"]


def test_distinct_ids_are_not_suppressed(agent):
    """Mutations on *different* ids must never trigger the dedup signal."""
    cache: dict[str, int] = {}
    messages: list[dict] = []

    agent._dedup_mutation_call("mark_read", {"message_id": "id-1"}, cache, messages)
    agent._dedup_mutation_call("mark_read", {"message_id": "id-2"}, cache, messages)
    agent._dedup_mutation_call("mark_read", {"message_id": "id-3"}, cache, messages)

    assert messages == []


def test_arg_order_does_not_defeat_dedup(agent):
    """Normalization: reordered kwargs hash to the same key."""
    cache: dict[str, int] = {}
    messages: list[dict] = []

    agent._dedup_mutation_call(
        "archive_message", {"message_id": "x", "debug": True}, cache, messages
    )
    agent._dedup_mutation_call(
        "archive_message", {"debug": True, "message_id": "x"}, cache, messages
    )

    assert len(messages) == 1


def test_batch_variants_are_covered(agent):
    """``*_batch`` mutation tools dedup on identical id lists too."""
    cache: dict[str, int] = {}
    messages: list[dict] = []
    args = {"message_ids": ["a", "b", "c"]}

    agent._dedup_mutation_call("mark_read_batch", args, cache, messages)
    agent._dedup_mutation_call("mark_read_batch", args, cache, messages)

    assert len(messages) == 1


def test_non_mutation_tool_is_ignored(agent):
    """Query/other tools are out of scope — helper is a no-op for them."""
    cache: dict[str, int] = {}
    messages: list[dict] = []
    args = {"query": "what is x"}

    agent._dedup_mutation_call("query_documents", args, cache, messages)
    agent._dedup_mutation_call("query_documents", args, cache, messages)

    assert messages == []
    assert cache == {}


def test_unhashable_args_do_not_raise(agent):
    """Non-JSON-serializable args fall back to ``str()`` without crashing."""
    cache: dict[str, int] = {}
    messages: list[dict] = []

    class _Weird:
        def __repr__(self) -> str:
            return "weird"

    args = {"message_id": "x", "ctx": _Weird()}
    agent._dedup_mutation_call("add_star", args, cache, messages)
    agent._dedup_mutation_call("add_star", args, cache, messages)

    assert len(messages) == 1
