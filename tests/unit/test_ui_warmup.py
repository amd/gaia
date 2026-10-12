# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Agent UI's warm-up primes exactly the prefix a new chat's first turn sends."""

from __future__ import annotations

import hashlib
import json
import threading

import numpy as np
import pytest

from gaia.ui import warmup

# ── PrefixWarmer ────────────────────────────────────────────────────────────


def _gate_job(result=None):
    release = threading.Event()
    calls = []

    def job():
        calls.append(1)
        release.wait(5)
        return result

    return job, release, calls


def test_one_warm_up_at_a_time():
    job, release, calls = _gate_job({"seconds": 1.0})
    warmer = warmup.PrefixWarmer(job)

    assert warmer.request("a") is True
    assert warmer.request("b") is False
    assert warmer.snapshot()["state"] == warmup.WARMING
    release.set()
    assert warmer.wait(5)
    assert calls == [1]
    assert warmer.snapshot()["state"] == warmup.READY


def test_a_repeat_right_after_a_warm_up_is_debounced():
    warmer = warmup.PrefixWarmer(lambda: {"seconds": 0.1})
    warmer.request("a")
    warmer.wait(5)
    assert warmer.request("b") is False


def test_never_starts_while_a_chat_turn_runs():
    calls = []
    warmer = warmup.PrefixWarmer(lambda: calls.append(1) or {})
    warmer.set_busy_check(lambda: True)
    assert warmer.request("a") is False
    assert warmer.snapshot()["state"] == warmup.IDLE
    assert calls == []


def test_a_failure_is_reported_and_retried_next_time(monkeypatch):
    warnings = []
    monkeypatch.setattr(
        warmup.logger, "warning", lambda msg, *a, **k: warnings.append(msg % a)
    )

    def boom():
        raise ConnectionError("Lemonade not reachable")

    warmer = warmup.PrefixWarmer(boom)
    warmer.request("a")
    warmer.wait(5)
    snap = warmer.snapshot()
    assert snap["state"] == warmup.FAILED
    assert "Lemonade not reachable" in snap["error"]
    assert warnings and "Warm-up (a) failed" in warnings[0]
    assert warmer.request("b") is True
    warmer.wait(5)


def test_nothing_to_warm_is_skipped():
    warmer = warmup.PrefixWarmer(lambda: None)
    warmer.request("a")
    warmer.wait(5)
    assert warmer.snapshot()["state"] == warmup.SKIPPED


def test_a_turn_waits_for_the_running_warm_up(monkeypatch):
    job, release, _calls = _gate_job({"seconds": 1.0})
    warmer = warmup.PrefixWarmer(job)
    monkeypatch.setattr(warmup, "_warmer", warmer)
    warmer.request("a")

    waited = threading.Event()

    def turn():
        warmup.wait_for_warm_up(timeout=5)
        waited.set()

    t = threading.Thread(target=turn)
    t.start()
    assert not waited.wait(0.2), "the turn must not race the warm-up"
    release.set()
    t.join(5)
    assert waited.is_set()


# ── Exact prefix ────────────────────────────────────────────────────────────

_DIM = 768


def _fake_embedding(text):
    vec = np.zeros(_DIM, dtype=np.float32)
    for word in str(text).lower().split():
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % _DIM] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


def _dump(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _prefix(messages, tools):
    """System message and tool schemas: what a warm-up leaves in the cache."""
    return _dump(messages[:1]) + _dump(tools)


@pytest.fixture
def ui_flagship(monkeypatch, tmp_path):
    pytest.importorskip("gaia_agent")
    from gaia_agent_chat.agent import ChatAgent

    from gaia.agents.base.memory import MemoryMixin
    from gaia.agents.base.tools import _TOOL_REGISTRY
    from gaia.agents.registry import AgentRegistry
    from gaia.llm import lemonade_client
    from gaia.ui import _chat_helpers as chat

    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon"))
    monkeypatch.setenv("GAIA_PROJECT_MAP_AUTO_INDEX", "0")
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9/api/v1")
    monkeypatch.delenv("GAIA_DYNAMIC_TOOLS", raising=False)
    monkeypatch.setattr(MemoryMixin, "_embed_text", lambda self, t: _fake_embedding(t))
    monkeypatch.setattr(
        ChatAgent,
        "_embed_texts_batch",
        lambda self, texts: np.stack([_fake_embedding(t) for t in texts]),
    )
    monkeypatch.setattr(
        lemonade_client.LemonadeClient,
        "get_model_info",
        lambda self, name: {"id": name, "downloaded": True},
    )
    monkeypatch.setattr(chat, "_maybe_load_expected_model", lambda *a, **k: None)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    saved = dict(_TOOL_REGISTRY)
    registry = AgentRegistry()
    registry.discover()
    monkeypatch.setattr(chat, "_agent_registry", registry)

    requests = []
    agents = []
    real_create = registry.create_agent

    def create_and_capture(*args, **kwargs):
        agent = real_create(*args, **kwargs)
        agents.append(agent)

        def fake_chat(messages, model=None, stream=False, **kw):
            if kw.get("tools"):
                requests.append(
                    (
                        json.loads(json.dumps(messages)),
                        json.loads(json.dumps(kw["tools"])),
                    )
                )
            reply = "OK"
            return iter([reply]) if stream else reply

        client = agent.chat.llm_client
        monkeypatch.setattr(client, "chat", fake_chat)
        for name, value in (
            ("get_last_usage", None),
            ("get_last_ttft_seconds", None),
            ("get_last_finish_reason", "stop"),
            ("get_last_reasoning", None),
        ):
            monkeypatch.setattr(client, name, lambda *a, _v=value, **k: _v)
        monkeypatch.setattr(agent.chat, "get_stats", lambda: {})
        return agent

    monkeypatch.setattr(registry, "create_agent", create_and_capture)

    class _Db:
        def get_setting(self, key, default=None):
            return default

    try:
        yield chat, registry, requests, agents, _Db()
    finally:
        for agent in agents:
            agent.close()
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_ui_warm_up_primes_a_new_chats_first_turn(ui_flagship):
    """The warm-up's prefix equals what a new chat's first turn sends.

    The new chat is built the way the streaming chat path builds a registered
    agent on a cache miss, under its own session id.
    """
    chat, registry, requests, agents, db = ui_flagship
    from gaia.ui.database import resolved_default_model

    result = warmup._warm_new_chat_prefix(db)
    assert result is not None and len(requests) == 1
    warm = requests[-1]

    session = {"model": resolved_default_model(), "agent_type": "gaia", "device": "gpu"}
    model_id, device_ctx = chat.resolve_session_model(session, "gaia", None, registry)
    agent = registry.create_agent(
        "gaia",
        **chat._build_create_kwargs(
            custom_model=None,
            model_id=model_id,
            streaming=True,
            device="gpu",
            min_context_size=device_ctx,
        ),
        **chat._session_agent_kwargs(
            rag_file_paths=[],
            library_paths=[],
            allowed=chat._compute_allowed_paths([]),
            session_id="0f6c2d0e-real-session",
            dynamic_tools=False,
            memory_incognito=chat._memory_off(session, db),
        ),
    )
    agent.process_query("hello")
    first = requests[-1]

    before, after = _prefix(*warm), _prefix(*first)
    at = next(
        (i for i, (a, b) in enumerate(zip(before, after)) if a != b),
        min(len(before), len(after)),
    )
    assert before == after, (
        f"warm-up prefix diverges from a new chat's first turn at char {at}:\n"
        f"  warm: {before[max(0, at - 60): at + 120]!r}\n"
        f"  turn: {after[max(0, at - 60): at + 120]!r}"
    )
