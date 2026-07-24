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

import json
from unittest.mock import MagicMock, patch

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


# ---------------------------------------------------------------------------
# Errored mutations are legitimate retries, not repeats (#2464)
# ---------------------------------------------------------------------------


def test_errored_mutation_retry_is_not_deduped(agent):
    """An identical call that ERRORED both times must not inject 'already
    applied' — the mutation never took effect, so re-issuing it is a valid
    retry, not a redundant repeat (#2464 batch archive/star dead-end)."""
    cache: dict[str, int] = {}
    messages: list[dict] = []
    args = {"message_ids": ["a", "b", "c"], "mailbox": "INBOX"}
    err = {
        "status": "error",
        "error": "Unexpected argument(s) for archive_message_batch: mailbox.",
    }

    agent._dedup_mutation_call("archive_message_batch", args, cache, messages, err)
    agent._dedup_mutation_call("archive_message_batch", args, cache, messages, err)

    # No false "already applied — move on" signal: the model stays free to
    # retry without the offending kwarg.
    assert messages == []
    assert cache == {}


def test_retry_after_error_then_success_still_dedups(agent):
    """Once a mutation actually applies, a later identical *successful* repeat
    is still deduped — the errored attempts don't poison the counter (#2464)."""
    cache: dict[str, int] = {}
    messages: list[dict] = []
    bad = {"message_ids": ["a"], "mailbox": "INBOX"}
    good = {"message_ids": ["a"]}
    err = {"status": "error", "error": "Unexpected argument(s): mailbox."}
    ok = {"status": "success", "total": 1}

    agent._dedup_mutation_call("add_star_batch", bad, cache, messages, err)
    agent._dedup_mutation_call("add_star_batch", good, cache, messages, ok)
    assert messages == []  # first successful apply — no signal

    # A redundant *successful* repeat of the good call is still caught.
    agent._dedup_mutation_call("add_star_batch", good, cache, messages, ok)
    assert len(messages) == 1
    assert "[SYSTEM]" in messages[0]["content"]


def test_success_default_result_preserves_legacy_dedup(agent):
    """Omitting ``tool_result`` (legacy callers) still dedups — the fix only
    changes behavior for explicitly errored results."""
    cache: dict[str, int] = {}
    messages: list[dict] = []
    args = {"message_id": "abc123"}

    agent._dedup_mutation_call("mark_read", args, cache, messages)
    agent._dedup_mutation_call("mark_read", args, cache, messages)

    assert len(messages) == 1


# ---------------------------------------------------------------------------
# End-to-end loop recovery (#2464 acceptance criterion 2)
# ---------------------------------------------------------------------------


def _stub_chat(agent, *responses):
    """Replace agent.chat with a stub that yields *responses* in order."""
    responses = list(responses)
    chat = MagicMock()

    def _send(*_, **__):
        r = responses.pop(0)
        resp = MagicMock()
        resp.text = r
        resp.stats = {}
        return resp

    chat.send_messages = MagicMock(side_effect=_send)
    agent.chat = chat
    return chat


def test_batch_mutation_recovers_from_spurious_kwarg_through_loop(agent):
    """Drive a batch mutation with a spurious ``mailbox`` kwarg through the
    REAL agent loop and assert the operation completes (#2464).

    The model emits ``add_star_batch(..., mailbox=...)`` twice — the
    dispatcher rejects the unexpected kwarg both times. Before the fix, the
    2nd identical (errored) call tripped ``_dedup_mutation_call`` into
    injecting "the change is already applied — move on", so the model
    finished with nothing starred. After the fix, errored calls are not
    deduped, so the model gets a clean retry and its 3rd (corrected) call
    actually stars the messages.
    """
    applied: list[list[str]] = []

    def _add_star_batch(message_ids):
        applied.append(list(message_ids))
        return {"status": "success", "total": len(message_ids)}

    agent._instance_tools = {
        "add_star_batch": {
            "name": "add_star_batch",
            "description": "Star multiple messages.",
            "parameters": {"message_ids": {"type": "array", "required": True}},
            "function": _add_star_batch,
            "atomic": True,
        }
    }

    bad = json.dumps(
        {
            "tool": "add_star_batch",
            "tool_args": {"message_ids": ["a", "b", "c"], "mailbox": "INBOX"},
        }
    )
    good = json.dumps(
        {"tool": "add_star_batch", "tool_args": {"message_ids": ["a", "b", "c"]}}
    )
    done = json.dumps({"answer": "Starred all 3 emails."})
    chat = _stub_chat(agent, bad, bad, good, done)

    result = agent.process_query("star these 3 emails: a, b, c", max_steps=8)

    # The corrected batch call actually ran exactly once — the operation
    # completed rather than dead-ending on the rejected kwarg.
    assert applied == [["a", "b", "c"]]
    # And the loop didn't bail early: it reached the corrected call + answer.
    assert chat.send_messages.call_count == 4
    text = result["result"] if isinstance(result, dict) else str(result)
    assert "Starred all 3 emails." in text

    # Regression guard: the 2nd rejected (errored) call must NOT have injected
    # a false "already applied — move on" dedup signal. That signal is what
    # abandoned the operation before the fix; a deterministic stub would run
    # the corrected call anyway, so assert the poison message never appeared.
    all_sent = [
        m.get("content", "")
        for call in chat.send_messages.call_args_list
        for m in call.kwargs.get("messages", [])
        if isinstance(m.get("content"), str)
    ]
    assert not any("already applied" in c for c in all_sent)
