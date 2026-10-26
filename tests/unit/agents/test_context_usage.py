# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``context_usage`` reports what the next model call holds, and what goes next.

It must agree with ``select_history`` — the meter promising one view while the
model gets another is worse than no meter.
"""

import json
from types import SimpleNamespace

import pytest

from gaia.agents.base import history
from gaia.agents.base.turn_metrics import count_tokens


def _agent(prompt="You are GAIA.", tools=None):
    return SimpleNamespace(
        model_id="M-GGUF",
        system_prompt=prompt,
        _openai_tools=tools or [{"name": "query_documents"}],
        chat=SimpleNamespace(config=SimpleNamespace(max_tokens=1024)),
    )


def _text_turn(question, answer_words=20):
    return [
        {"role": "user", "content": question},
        {"role": "assistant", "content": " ".join(["word"] * answer_words)},
    ]


def _document_turn(question, chunk_words):
    call = {
        "id": "c1",
        "type": "function",
        "function": {"name": "query_documents", "arguments": "{}"},
    }
    return [
        {"role": "user", "content": question},
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {
            "role": "tool",
            "tool_call_id": "c1",
            "name": "query_documents",
            "content": " ".join(["chunk"] * chunk_words),
        },
        {"role": "assistant", "content": "It says so."},
    ]


@pytest.fixture
def window(monkeypatch):
    def set_window(tokens):
        monkeypatch.setattr(history, "context_window", lambda _agent: tokens)

    set_window(32768)
    return set_window


def test_a_long_document_lands_in_documents_not_conversation(window):
    agent = _agent()
    before = history.context_usage(agent, [_text_turn("hi")])
    after = history.context_usage(
        agent, [_text_turn("hi"), _document_turn("summarise report.pdf", 2000)]
    )

    assert before["documents"] == 0
    assert after["documents"] > 1500
    assert after["documents"] > after["history"]
    assert after["tools"] > before["tools"]
    assert after["used"] == sum(
        after[k] for k in ("system", "tools", "documents", "history", "query")
    )
    assert after["used"] > before["used"]


def test_the_view_matches_what_select_history_replays(window):
    window(6000)
    agent = _agent()
    turns = [_text_turn(f"question {i}", 300) for i in range(8)]

    usage = history.context_usage(agent, turns, "next")
    replayed = history.select_history(turns, history.history_budget(agent, "next"))

    assert usage["turns_in_view"] * 2 == len(replayed)
    assert usage["turns_hidden"] == 8 - usage["turns_in_view"]
    assert usage["history_used"] == sum(
        count_tokens(json.dumps(t, ensure_ascii=False))
        for t in turns[-usage["turns_in_view"] :]
    )


def test_warns_with_the_oldest_questions_before_they_are_dropped(window):
    window(6000)
    agent = _agent()
    budget = history.history_budget(agent, "")
    turns = []
    while True:
        turns.append(_text_turn(f"question {len(turns)}", 120))
        usage = history.context_usage(agent, turns)
        if usage["at_risk"]:
            break
        assert len(turns) < 50, "never warned"

    assert usage["history_used"] >= budget * history.HISTORY_WARN_FRACTION
    # Nothing is gone yet — the warning comes first.
    assert usage["turns_hidden"] == 0
    named = usage["at_risk"]
    assert named == [f"question {i}" for i in range(len(named))]
    assert len(named) < len(turns)
    # Exactly the turns select_history drops once the next turn overflows it.
    retained = sum(
        count_tokens(json.dumps(t, ensure_ascii=False)) for t in turns[len(named) :]
    )
    assert retained <= budget // 2


def test_no_warning_while_history_has_room(window):
    usage = history.context_usage(_agent(), [_text_turn("q")])
    assert usage["at_risk"] == []
    assert usage["at_risk_tokens"] == 0


def test_text_rendered_tool_results_are_classified_too(window):
    turns = history.text_tool_history([_document_turn("read it", 500)])
    usage = history.context_usage(_agent(), turns)
    assert usage["documents"] > 400


def test_measuring_reports_no_cut(window, caplog):
    window(3000)
    turns = [_text_turn(f"q{i}", 400) for i in range(10)]
    with caplog.at_level("WARNING"):
        history.context_usage(_agent(), turns)
    assert "Context budget excluded" not in caplog.text
