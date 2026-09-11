# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Evidence survives retries, cancellation, persistence and follow-up turns."""

import json
from unittest.mock import patch

import pytest

from gaia.agents.base.history import TurnMessages, select_history, transcript_turns
from tests.unit.agents import test_parallel_tool_calls
from tests.unit.agents.test_parallel_tool_calls import (
    _native_envelope,
    _register_tool,
    _stub_chat,
)

agent = test_parallel_tool_calls.agent
clean_registry = test_parallel_tool_calls.clean_registry


def _turn(index):
    return [
        {"role": "user", "content": f"question-{index}"},
        {"role": "assistant", "content": f"answer-{index}"},
    ]


def test_history_keeps_more_than_five_complete_turns():
    turns = [_turn(i) for i in range(30)]
    assert select_history(turns, 10000) == sum(turns, [])


def test_block_eviction_keeps_prefix_stable_until_budget_crossed():
    turns = [_turn(i) for i in range(12)]
    with patch("gaia.agents.base.history.count_tokens", return_value=10):
        assert select_history(turns[:10], 100) == sum(turns[:10], [])
        assert select_history(turns[:11], 100) == sum(turns[6:11], [])
        assert select_history(turns[:12], 100) == sum(turns[6:12], [])


def test_oversized_turn_is_not_split_or_silently_truncated():
    turns = [_turn(0), [{"role": "user", "content": "x" * 10000}], _turn(2)]
    with patch("gaia.agents.base.history.logger") as logger:
        assert select_history(turns, 100) == _turn(2)
    logger.warning.assert_called_once()


def test_impossible_budget_fails_loudly():
    with pytest.raises(ValueError, match="No context budget"):
        select_history([_turn(0)], 0)


def test_legacy_and_recorded_turns_do_not_duplicate_user_input():
    trace = _turn(1)
    records = _turn(0) + [trace[0], {**trace[1], "model_messages": trace}]
    records.append({"role": "user", "content": "current unanswered query"})
    assert transcript_turns(records) == [_turn(0), trace]


def test_autonomous_trace_is_retained():
    assert transcript_turns(
        [{"role": "autonomous", "content": "tick", "model_messages": _turn(0)}]
    ) == [_turn(0)]


def test_snapshot_survives_overflow_and_external_mutation():
    history = TurnMessages()
    message = {"role": "user", "content": [{"type": "text", "text": "original"}]}
    history.append(message)
    message["content"][0]["text"] = "mutated"
    history[:] = [{"role": "user", "content": "overflow stub"}]
    history.append({"role": "assistant", "content": "raw envelope"})
    saved = history.finish("final answer")
    assert saved[0]["content"][0]["text"] == "original"
    assert saved[-1]["content"] == "final answer"
    saved[0]["content"][0]["text"] = "caller mutation"
    assert history.finish("")[0]["content"][0]["text"] == "original"


def test_partial_fanout_records_missing_results_before_answer():
    history = TurnMessages()
    history.append({"role": "user", "content": "read files"})
    history.append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": name,
                    "type": "function",
                    "function": {"name": name, "arguments": "{}"},
                }
                for name in ("first", "second")
            ],
        }
    )
    history.append({"role": "tool", "tool_call_id": "first", "content": "evidence"})
    saved = history.finish("")
    assert saved[2]["content"] == "evidence"
    assert saved[3]["tool_call_id"] == "second"
    assert "Do not assume it succeeded" in saved[3]["content"]
    assert saved[4] == {"role": "assistant", "content": ""}


def test_real_agent_returns_only_current_turn_with_tool_evidence(agent):
    _register_tool("read_fact", lambda: {"fact": "violet-otter-92"})
    agent.conversation_history = _turn("old")
    _stub_chat(
        agent, _native_envelope(("call-1", "read_fact", {})), "The fact was read."
    )
    result = agent.process_query("Read the fact", max_steps=4)
    messages = result["model_messages"]
    assert messages[0] == {"role": "user", "content": "Read the fact"}
    assert "question-old" not in json.dumps(messages)
    assert messages[1]["tool_calls"][0]["id"] == "call-1"
    assert messages[2]["tool_call_id"] == "call-1"
    assert "violet-otter-92" in json.dumps(messages[2])
    assert messages[-1]["content"] == result["result"]


def test_real_agent_max_steps_preserves_tool_evidence_and_actual_answer(agent):
    _register_tool("read_fact", lambda: {"fact": "desk-17C"})
    _stub_chat(agent, _native_envelope(("call-1", "read_fact", {})))
    result = agent.process_query("Read the fact", max_steps=1)
    assert "desk-17C" in json.dumps(result["model_messages"])
    assert result["model_messages"][-1]["content"] == result["result"]


def test_legacy_tool_evidence_does_not_replay_as_orphan_native_result(agent):
    _register_tool("read_fact", lambda: {"fact": "legacy-evidence-17C"})
    _stub_chat(agent, '{"tool": "read_fact", "tool_args": {}}', "Read it.")
    result = agent.process_query("Read fact", max_steps=4)
    messages = result["model_messages"]
    assert "legacy-evidence-17C" in json.dumps(messages)
    assert not any(m["role"] == "tool" for m in messages)
    assert any("Recorded tool result" in str(m.get("content")) for m in messages)


def test_restoration_uses_actual_agent_native_tool_property(agent):
    from types import SimpleNamespace

    from gaia.ui._chat_helpers import _restore_model_history
    from gaia.ui.database import ChatDatabase

    db = ChatDatabase(":memory:")
    session = db.create_session()["id"]
    db.add_message(session, "user", "prior question")
    db.add_message(session, "assistant", "prior answer")
    agent.chat.config = SimpleNamespace(max_tokens=8192)
    _restore_model_history(agent, db, session, "follow up")
    assert agent.conversation_history[-1]["content"] == "prior answer"
    db.close()
