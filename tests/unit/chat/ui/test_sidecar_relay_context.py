# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A relayed flagship turn carries the session's earlier turns.

A Hub binary install runs the flagship as a daemon sidecar, and the sidecar
sees only the transcript the UI pushes in ``context``. When the UI built that
transcript for email alone, every follow-up reached the flagship with no
history, and it answered "that same country?" as if it were the first message.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

import gaia.ui._chat_helpers as chat_helpers_module
from gaia.agents.registry import AgentRegistry
from gaia.hub.installer import register_installed_sidecars
from gaia.ui._chat_helpers import _stream_chat_impl
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest


@pytest.fixture
def sidecar_flagship_registry(tmp_path, monkeypatch):
    """The registry after a binary Hub install of the flagship — no wheel."""
    root = tmp_path / "agents"
    (root / "gaia").mkdir(parents=True)
    (root / "gaia" / ".installed").write_text(
        json.dumps(
            {
                "id": "gaia",
                "version": "0.2.0",
                "language": "python",
                "installed_at": "2026-09-23T00:00:00Z",
                "artifact_sha256": "0" * 64,
                "artifact_kind": "binary",
                "executable": "gaia-agent-gaia",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "gaia.hub.installer.default_install_root", lambda: root, raising=False
    )
    registry = AgentRegistry()
    registry._agents.clear()
    register_installed_sidecars(registry)
    assert registry.get("gaia").is_sidecar is True
    return registry


async def _noop_retitle(**kwargs):
    return None


def _relay_one_turn(monkeypatch, registry, agent_type, prior_turns, message):
    captured = {}

    def _fake_dispatch(sse_handler, request, history_pairs, model_id, agent, **kw):
        captured["history_pairs"] = history_pairs
        captured["agent_type"] = agent
        sse_handler._emit({"type": "answer", "content": "ok"})

    monkeypatch.setattr(chat_helpers_module, "_agent_registry", registry)
    monkeypatch.setattr(chat_helpers_module, "_dispatch_sidecar_query", _fake_dispatch)
    monkeypatch.setattr(
        chat_helpers_module, "_maybe_update_session_title", _noop_retitle
    )

    db = ChatDatabase(":memory:")
    session = db.create_session(agent_type=agent_type)
    for user_msg, assistant_msg in prior_turns:
        db.add_message(session["id"], "user", user_msg)
        db.add_message(session["id"], "assistant", assistant_msg)
    request = ChatRequest(
        session_id=session["id"], message=message, stream=True, agent_type=agent_type
    )
    run = SimpleNamespace(handler=None)

    async def _drive():
        async for _ in _stream_chat_impl(run, db, session, request):
            pass

    asyncio.run(_drive())
    return captured


def test_a_relayed_flagship_follow_up_gets_the_earlier_turns(
    monkeypatch, sidecar_flagship_registry
):
    prior = [("What is the capital of Australia?", "Canberra.")]
    captured = _relay_one_turn(
        monkeypatch,
        sidecar_flagship_registry,
        "gaia",
        prior,
        "And the second-largest city in that same country?",
    )

    assert captured["agent_type"] == "gaia"
    assert captured["history_pairs"] == prior


def test_email_still_gets_its_transcript(monkeypatch, sidecar_flagship_registry):
    prior = [("Any mail from Ana?", "Two messages.")]
    captured = _relay_one_turn(
        monkeypatch, sidecar_flagship_registry, "email", prior, "Summarise them."
    )

    assert captured["history_pairs"] == prior


def test_a_relayed_turn_is_measured_for_under_the_hood(
    monkeypatch, sidecar_flagship_registry
):
    """The sidecar's timings on ``final`` reach the Context panel's turn record."""
    from gaia.ui.email_sidecar.profiles import profile_for
    from gaia.ui.email_sidecar.relay import _dispatch_one
    from gaia.ui.turn_insights import turn_record

    stats = {
        "type": "stats",
        "step": 1,
        "performance_stats": {"prompt_tokens": 1200, "completion_tokens": 40},
    }
    result = {
        "answer": "Canberra.",
        "timing_summary": {"llm_seconds": 2.5, "tool_seconds": 0.0, "llm_calls": 1},
        "conversation": [
            {"role": "user", "content": "Capital of Australia?"},
            {"role": "system", "content": stats},
        ],
    }

    def _fake_dispatch(sse_handler, request, history_pairs, model_id, agent, **kw):
        final = {
            "type": "final",
            "answer": "Canberra.",
            "performance": turn_record(result),
        }
        _dispatch_one(sse_handler, final, profile_for("gaia"))

    monkeypatch.setattr(
        chat_helpers_module, "_agent_registry", sidecar_flagship_registry
    )
    monkeypatch.setattr(chat_helpers_module, "_dispatch_sidecar_query", _fake_dispatch)
    monkeypatch.setattr(
        chat_helpers_module, "_maybe_update_session_title", _noop_retitle
    )
    db = ChatDatabase(":memory:")
    session = db.create_session(agent_type="gaia")
    request = ChatRequest(
        session_id=session["id"],
        message="Capital of Australia?",
        stream=True,
        agent_type="gaia",
    )

    async def _drive():
        run = SimpleNamespace(handler=None)
        async for _ in _stream_chat_impl(run, db, session, request):
            pass

    asyncio.run(_drive())

    [turn] = db.turn_performance(session["id"])
    assert turn["model_calls"] == 1
    assert turn["tokens_in"] == 1200
    assert turn["tokens_out"] == 40
    assert turn["time"]["model_seconds"] is not None


def _relay_final(monkeypatch, registry, final):
    """Drive one relayed turn whose sidecar ends on ``final``; return the session's turns."""
    from gaia.ui.email_sidecar.profiles import profile_for
    from gaia.ui.email_sidecar.relay import _dispatch_one

    def _fake_dispatch(sse_handler, request, history_pairs, model_id, agent, **kw):
        _dispatch_one(sse_handler, final, profile_for("gaia"))

    monkeypatch.setattr(chat_helpers_module, "_agent_registry", registry)
    monkeypatch.setattr(chat_helpers_module, "_dispatch_sidecar_query", _fake_dispatch)
    monkeypatch.setattr(
        chat_helpers_module, "_maybe_update_session_title", _noop_retitle
    )
    db = ChatDatabase(":memory:")
    session = db.create_session(agent_type="gaia")
    request = ChatRequest(
        session_id=session["id"], message="Hi", stream=True, agent_type="gaia"
    )

    async def _drive():
        async for _ in _stream_chat_impl(
            SimpleNamespace(handler=None), db, session, request
        ):
            pass

    asyncio.run(_drive())
    messages = db.get_messages(session["id"])
    return db.turn_performance(session["id"]), messages


def test_an_older_sidecar_turn_stays_unmeasured(monkeypatch, sidecar_flagship_registry):
    """No ``performance`` on ``final``: the answer is saved and no figures are invented."""
    turns, messages = _relay_final(
        monkeypatch,
        sidecar_flagship_registry,
        {"type": "final", "answer": "Hello."},
    )

    assert turns == []
    assert any(m["role"] == "assistant" and "Hello." in m["content"] for m in messages)


@pytest.mark.parametrize(
    "performance",
    [
        "fast",
        [],
        {"timing_summary": "2.5s", "conversation": []},
        {"timing_summary": {}, "conversation": "stats"},
        {"timing_summary": {}, "conversation": ["stats"]},
        {"timing_summary": {}, "conversation": [{"role": "system", "content": "x"}]},
        {
            "timing_summary": {},
            "conversation": [
                {"role": "system", "content": {"type": "stats", "performance_stats": 7}}
            ],
        },
        {
            "timing_summary": {},
            "conversation": [
                {"role": "system", "content": {"type": "stats", "tools": ["grep"]}}
            ],
        },
    ],
)
def test_a_malformed_performance_record_never_breaks_the_turn(
    monkeypatch, sidecar_flagship_registry, performance
):
    turns, messages = _relay_final(
        monkeypatch,
        sidecar_flagship_registry,
        {"type": "final", "answer": "Hello.", "performance": performance},
    )

    assert turns == []
    assert any(m["role"] == "assistant" and "Hello." in m["content"] for m in messages)


def _long_turn(steps, tool_name="search_web"):
    stats = {
        "type": "stats",
        "step": 1,
        "performance_stats": {"prompt_tokens": 1200, "completion_tokens": 40},
        "tools": [{"name": tool_name, "seconds": 0.5}],
    }
    return {
        "timing_summary": {"llm_seconds": 2.5, "tool_seconds": 0.5, "llm_calls": 1},
        "conversation": [{"role": "system", "content": stats}] * steps,
    }


@pytest.mark.parametrize(
    "wire",
    [
        '{"timing_summary": {"llm_calls": Infinity}, "conversation": []}',
        '{"timing_summary": {"llm_calls": -Infinity}, "conversation": []}',
        '{"timing_summary": {"llm_seconds": NaN, "llm_calls": 1}, "conversation": []}',
    ],
)
def test_a_non_finite_number_never_loses_the_answer(
    monkeypatch, sidecar_flagship_registry, wire, caplog
):
    """json.loads accepts Infinity/NaN; int(inf) used to escape and drop the answer."""
    caplog.set_level("WARNING", logger="gaia.ui.email_sidecar.relay")
    turns, messages = _relay_final(
        monkeypatch,
        sidecar_flagship_registry,
        {"type": "final", "answer": "Hello.", "performance": json.loads(wire)},
    )

    assert "value JSON can't carry" in caplog.text
    assert turns == []
    assert any(m["role"] == "assistant" and "Hello." in m["content"] for m in messages)


@pytest.mark.parametrize(
    "performance",
    [
        _long_turn(20_000, tool_name="t" * 1_000),
        _long_turn(1, tool_name="t" * 5_000_000),
    ],
    ids=["many-steps", "one-huge-name"],
)
def test_an_oversized_record_is_not_stored(
    monkeypatch, sidecar_flagship_registry, performance, caplog
):
    caplog.set_level("WARNING", logger="gaia.ui.email_sidecar.relay")
    turns, messages = _relay_final(
        monkeypatch,
        sidecar_flagship_registry,
        {"type": "final", "answer": "Hello.", "performance": performance},
    )

    size = len(json.dumps(performance))
    assert f"{size:,} bytes, over the 1,000,000-byte limit" in caplog.text
    assert turns == []
    assert any(m["role"] == "assistant" and "Hello." in m["content"] for m in messages)


def test_a_long_real_turn_is_still_measured(monkeypatch, sidecar_flagship_registry):
    """The size cap leaves room for a turn far longer than the step limit allows."""
    from gaia.ui.turn_insights import MAX_TURN_RECORD_BYTES

    performance = _long_turn(500)
    assert len(json.dumps(performance)) < MAX_TURN_RECORD_BYTES
    turns, _ = _relay_final(
        monkeypatch,
        sidecar_flagship_registry,
        {"type": "final", "answer": "Hello.", "performance": performance},
    )

    [turn] = turns
    assert turn["tokens_in"] == 1200 * 500


def test_a_summary_that_overflows_still_saves_the_answer(
    monkeypatch, sidecar_flagship_registry
):
    import gaia.ui.turn_insights as turn_insights

    def _overflow(*args, **kwargs):
        raise OverflowError("cannot convert float infinity to integer")

    monkeypatch.setattr(turn_insights, "summarize_turn", _overflow)
    turns, messages = _relay_final(
        monkeypatch,
        sidecar_flagship_registry,
        {"type": "final", "answer": "Hello.", "performance": _long_turn(1)},
    )

    assert turns == []
    assert any(m["role"] == "assistant" and "Hello." in m["content"] for m in messages)


def test_non_numeric_timings_are_shown_as_unknown_not_guessed(
    monkeypatch, sidecar_flagship_registry
):
    stats = {
        "type": "stats",
        "step": 1,
        "performance_stats": {"prompt_tokens": "lots", "completion_tokens": -3},
    }
    turns, _ = _relay_final(
        monkeypatch,
        sidecar_flagship_registry,
        {
            "type": "final",
            "answer": "Hello.",
            "performance": {
                "timing_summary": {"llm_seconds": "NaN", "llm_calls": 1},
                "conversation": [{"role": "system", "content": stats}],
            },
        },
    )

    [turn] = turns
    assert turn["tokens_in"] is None and turn["tokens_out"] is None
    assert turn["time"]["model_seconds"] is None
