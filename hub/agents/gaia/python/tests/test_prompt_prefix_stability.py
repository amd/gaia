# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every request the flagship sends must extend the one before it.

llama.cpp reuses its cache only for the prefix two requests share. Gemma 4's
sliding-window attention and Qwen3.6's hybrid layers can resume only from a
checkpoint near the end of an earlier prompt, so a byte that changes anywhere
in the system prompt or tool list makes the server re-read the whole
conversation: 34K tokens and 155 s for one step of a v0.25 RC session.

This drives the real flagship through the stdio transport's history path with
the model call captured, over the things that used to rewrite the prompt
mid-session: indexing a document, a learned procedure being recalled, and
background memory extraction storing a fact between turns.
"""

from __future__ import annotations

import json

from gaia_agent import stdio
from gaia_agent.agent import GaiaAgentConfig


def _dump_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _render(request) -> str:
    """The request as the chat template lays it out: system, tools, then turns."""
    messages, tools = request
    return (
        _dump_json(messages[:1])
        + _dump_json(tools)
        + "".join(_dump_json(m) for m in messages[1:])
    )


def _first_difference(before: str, after: str) -> int:
    for index, (a, b) in enumerate(zip(before, after)):
        if a != b:
            return index
    return min(len(before), len(after))


def _turn(agent, model, query, script):
    model.script[:] = list(script)
    agent.conversation_history.prepare(agent, query)
    result = agent.process_query(query)
    stdio._record_turn(agent, query, result["result"], result)
    assert not model.script, f"turn ended before using its script: {model.script}"


def test_each_request_extends_the_previous_one(flagship):
    agent, model, doc = flagship

    _turn(agent, model, "Hi, I'm Sam and I work on the safety team.", ["Hi Sam."])
    # What background extraction does after a turn.
    agent._memory_store.store(
        category="fact", content="Sam works on the safety team", source="extraction"
    )
    _turn(
        agent,
        model,
        f"Index {doc} and tell me what the handbook says.",
        [
            {"id": "c1", "name": "index_document", "args": {"file_path": str(doc)}},
            "Wear goggles in the lab.",
            # A document answer that skipped retrieval is re-asked with the
            # framework's own lookup appended as a new message.
            "Wear goggles in the lab.",
        ],
    )
    _turn(
        agent,
        model,
        "Read handbook.md again.",
        [
            {"id": "c2", "name": "read_file", "args": {"file_path": str(doc)}},
            "It says to wear goggles.",
        ],
    )
    _turn(agent, model, "Thanks!", ["You're welcome."])

    rendered = [_render(request) for request in model.requests]
    assert len(rendered) == 7
    for step, (before, after) in enumerate(zip(rendered, rendered[1:]), start=1):
        at = _first_difference(before, after)
        assert after.startswith(before), (
            f"request {step + 1} stops extending request {step} at char {at} "
            f"of {len(before)}:\n  was: {before[max(0, at - 60): at + 120]!r}\n"
            f"  now: {after[max(0, at - 60): at + 120]!r}"
        )

    # Nothing the prompt used to carry was lost: it rides in the turn instead.
    turn_two = model.requests[1][0][-1]["content"]
    assert "RECALLED PROCEDURES" in turn_two and "read-the-handbook" in turn_two
    turn_three = [m for m in model.requests[4][0] if m["role"] == "user"][-1]
    assert "[Indexed documents: handbook.md (" in turn_three["content"]
    assert "Sam works on the safety team" not in model.requests[-1][0][0]["content"]


def _prefix(request) -> str:
    """System message and tool schemas: what a warm-up leaves in the cache."""
    messages, tools = request
    return _dump_json(messages[:1]) + _dump_json(tools)


def _assert_same_prefix(warm, first):
    before, after = _prefix(warm), _prefix(first)
    at = _first_difference(before, after)
    assert before == after, (
        f"warm-up prefix diverges from the first turn's at char {at} of "
        f"{len(after)}:\n  warm: {before[max(0, at - 60): at + 120]!r}\n"
        f"  turn: {after[max(0, at - 60): at + 120]!r}"
    )


def test_warm_up_primes_the_first_turns_exact_prefix(flagship):
    agent, model, _doc = flagship
    model.script[:] = ["OK"]
    agent.warm_up()
    warm = model.requests[-1]
    assert warm[0][0]["role"] == "system" and warm[1], "warm-up sent no prefix"

    _turn(agent, model, "hello", ["Hi."])
    _assert_same_prefix(warm, model.requests[-1])
