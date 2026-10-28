# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Evidence survives retries, cancellation, persistence and follow-up turns."""

import json
from unittest.mock import patch

import pytest

from gaia.agents.base import history
from gaia.agents.base.history import (
    CutReporter,
    TurnMessages,
    select_history,
    transcript_turns,
)
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


def _tool_turn(index):
    call_id = f"call-{index}"
    return [
        {"role": "user", "content": f"read fact {index}"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "read_fact", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "name": "read_fact",
            "content": f"fact-{index}",
        },
        {"role": "assistant", "content": f"The fact is fact-{index}."},
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


def test_a_cut_is_reported_once_with_its_size():
    turns = [_turn(i) for i in range(13)]
    cuts = []
    with patch("gaia.agents.base.history.count_tokens", return_value=10):
        for n in range(1, 14):
            select_history(turns[:n], 100, cuts.append)
    # Turn 11 overflows the budget and evicts the six oldest; 12 and 13 fit.
    assert [(c.turns, c.tokens) for c in cuts] == [(6, 60)]
    assert cuts[0].notice.startswith(
        "Older messages are now out of the model's view (6 turns)."
    )


def test_no_cut_reports_nothing():
    cuts = []
    select_history([_turn(i) for i in range(30)], 10000, cuts.append)
    assert cuts == []


def test_a_smaller_budget_that_hides_turns_is_reported():
    turns = [_turn(i) for i in range(10)]
    cuts = []
    reporter = CutReporter()
    with patch("gaia.agents.base.history.count_tokens", return_value=10):
        select_history(turns, 1000, cuts.append, reporter)
        # A smaller model: the same turns no longer fit, though no turn was added.
        select_history(turns, 60, cuts.append, reporter)
        # Back to the larger one, then the same small one again.
        select_history(turns, 1000, cuts.append, reporter)
        select_history(turns, 60, cuts.append, reporter)
        # A restart sees the stable cut fresh and does not re-announce it.
        select_history(turns, 60, cuts.append, CutReporter())
    assert [(c.turns, c.tokens) for c in cuts] == [(4, 40), (4, 40)]


def test_no_budget_left_reports_every_hidden_turn():
    cuts = []
    with patch("gaia.agents.base.history.count_tokens", return_value=10):
        assert select_history([_turn(i) for i in range(3)], 0, cuts.append) == []
        select_history([], 0, cuts.append)
    assert [(c.turns, c.tokens) for c in cuts] == [(3, 30)]


def _bulky_tool_turn(answer):
    turn = _tool_turn(9)
    turn[0]["content"] = "summarize the report"
    turn[2]["content"] = "revenue table " * 2000
    turn[3]["content"] = answer
    return turn


def test_newest_oversized_turn_keeps_its_question_and_answer(caplog):
    turns = [_turn(0), _turn(1), _bulky_tool_turn("revenue grew 12%")]

    replayed = select_history(turns, 1000)

    assert replayed[-4]["content"] == "summarize the report"
    assert replayed[-1] == {"role": "assistant", "content": "revenue grew 12%"}
    call, result = replayed[-3], replayed[-2]
    assert result["tool_call_id"] == call["tool_calls"][0]["id"] == "call-9"
    assert result["content"].startswith("[Left out of the conversation history")
    assert "read_fact" in result["content"]
    assert len(result["content"]) < 400
    # The small turns before it still fit beside the shortened one.
    assert replayed[:4] == _turn(0) + _turn(1)
    assert "shortened 1 turn(s)" in caplog.text


def test_oversized_turn_in_the_middle_keeps_the_turns_before_it():
    turns = [_turn(0), _turn(1), _bulky_tool_turn("revenue grew 12%"), _turn(3)]

    replayed = select_history(turns, 1000)

    assert replayed[:4] == _turn(0) + _turn(1)
    assert replayed[-2:] == _turn(3)
    assert {"role": "assistant", "content": "revenue grew 12%"} in replayed


def test_oversized_text_turn_is_cut_with_a_marker_not_dropped():
    # Pushed /query context carries no tool messages, only text.
    question = "here is the log: " + "line " * 5000
    turns = [_turn(0), [{"role": "user", "content": question}, *_turn(1)[1:]]]

    replayed = select_history(turns, 1000)

    assert history.turn_cost(replayed[2:]) <= 500
    assert replayed[2]["content"].startswith("here is the log: ")
    assert "characters to fit the model's context window" in replayed[2]["content"]
    assert replayed[-1] == {"role": "assistant", "content": "answer-1"}
    assert replayed[:2] == _turn(0)
    # The stored turn is untouched; only the replayed copy is cut.
    assert turns[1][0]["content"] == question


def test_text_tool_results_are_stubbed_too():
    turn = history.text_tool_history([_bulky_tool_turn("revenue grew 12%")])[0]

    replayed = select_history([turn], 1000)

    result = replayed[2]["content"]
    assert result.startswith("[Recorded tool result: read_fact] [Left out of")
    assert replayed[-1]["content"] == "revenue grew 12%"


def test_a_refusal_in_an_oversized_turn_is_not_stubbed():
    turn = _bulky_tool_turn("I did not run it.")
    refusal = json.dumps(
        {"status": "denied", "message": "User refused: " + "reason " * 100}
    )
    turn[2]["content"] = refusal
    turn[0]["content"] = "run it " * 3000

    replayed = select_history([turn], 1000)

    assert replayed[2]["content"] == refusal
    assert replayed[-1]["content"] == "I did not run it."


@pytest.mark.parametrize("as_text", [False, True])
def test_a_refusal_longer_than_the_question_is_cut_last(as_text):
    turn = _bulky_tool_turn("I did not run it.")
    refusal = json.dumps(
        {"status": "denied", "message": "User refused: " + "reason " * 600}
    )
    turn[2]["content"] = refusal
    turn[0]["content"] = "run it " * 400
    if as_text:
        turn = history.text_tool_history([turn])[0]
    whole = turn[2]["content"]
    budget = history.turn_cost(turn) - 100

    clipped, clips = history.clip_turn(turn, budget)

    assert clipped[2]["content"] == whole
    assert [index for index, _, _ in clips] == [0]
    assert history.turn_cost(clipped) <= budget


def test_a_refusal_is_still_cut_when_nothing_else_is_left():
    refusal = json.dumps({"status": "denied", "message": "no " * 3000})
    turn = [
        {"role": "user", "content": "run it"},
        {"role": "tool", "tool_call_id": "c", "name": "x", "content": refusal},
    ]

    clipped, clips = history.clip_turn(turn, 200)

    assert [index for index, _, _ in clips] == [1]
    assert history.turn_cost(clipped) <= 200


def test_a_turn_that_cannot_be_shortened_enough_is_dropped_alone(caplog):
    # Call arguments are replayed as sent, so only a tool result can shrink.
    huge = _tool_turn(1)
    huge[1]["tool_calls"][0]["function"]["arguments"] = json.dumps(
        {"text": "x" * 20000}
    )
    turns = [_turn(0), huge, _turn(2)]

    assert select_history(turns, 1000) == _turn(0) + _turn(2)
    assert "dropped 1 that still did not fit" in caplog.text


def test_eviction_never_separates_a_tool_call_from_its_result():
    turns = [_tool_turn(i) for i in range(12)]
    whole = sum(len(turn) for turn in turns)
    overflowed = 0
    # Only some budgets land the cut between a call and its result, so sweep.
    for budget in range(200, 1200, 10):
        window = select_history(turns, budget)
        if not 0 < len(window) < whole:
            continue
        overflowed += 1
        requested = {call["id"] for m in window for call in m.get("tool_calls") or []}
        answered = {m["tool_call_id"] for m in window if m.get("role") == "tool"}
        assert requested == answered, budget
    assert overflowed > 50


@pytest.mark.parametrize("budget", [0, -100])
def test_impossible_budget_warns_and_preserves_persisted_turns(caplog, budget):
    turns = [_turn(0)]
    assert select_history(turns, budget) == []
    assert turns == [_turn(0)]
    assert "No context budget remains" in caplog.text


def test_legacy_and_recorded_turns_do_not_duplicate_user_input():
    trace = _turn(1)
    records = _turn(0) + [trace[0], {**trace[1], "model_messages": trace}]
    records.append({"role": "user", "content": "current unanswered query"})
    assert transcript_turns(records) == [_turn(0), trace]


def test_unrecorded_turn_keeps_its_question_when_a_follow_up_was_added():
    # A turn stopped after taking a mid-reply message saves no model messages.
    records = [
        {"role": "user", "content": "original question"},
        {"role": "user", "content": "added mid-reply"},
        {"role": "assistant", "content": "partial answer"},
    ]
    assert transcript_turns(records) == [
        [
            {"role": "user", "content": "original question\n\nadded mid-reply"},
            {"role": "assistant", "content": "partial answer"},
        ]
    ]


def test_follow_ups_do_not_leak_across_turns():
    trace = _turn(0)
    records = [
        trace[0],
        {"role": "user", "content": "added to turn 0"},
        {**trace[1], "model_messages": trace},
        {"role": "user", "content": "question-1"},
        {"role": "user", "content": "added to turn 1"},
        {"role": "user", "content": "added again"},
        {"role": "assistant", "content": "answer-1"},
        {"role": "user", "content": "current unanswered query"},
    ]
    assert transcript_turns(records) == [
        trace,
        [
            {
                "role": "user",
                "content": "question-1\n\nadded to turn 1\n\nadded again",
            },
            {"role": "assistant", "content": "answer-1"},
        ],
    ]


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
