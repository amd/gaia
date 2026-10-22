# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a relayed flagship turn keeps on its way to the Agent UI.

The Electron app relays every chat to the flagship sidecar. The relay used to
drop three things the in-process path keeps: the turn's token usage (so a
Fireworks chat showed no tokens and no cost), a permission prompt's arguments
(so code was approved from a shortened summary and a path prompt said an
existing file "doesn't exist yet"), and the chat's private / memory-off state
(so the agent saved memories from a private chat).
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from gaia.agents.base.memory import MemoryMixin
from gaia.llm.inference_location import InferenceLocation
from gaia.ui import _chat_helpers as helpers
from gaia.ui.email_sidecar import daemon_client as daemon_client_module
from gaia.ui.email_sidecar import relay
from gaia.ui.email_sidecar.profiles import (
    EMAIL_PROFILE,
    GAIA_PROFILE,
    memory_supported,
)
from gaia.ui.models import ChatRequest
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import (
    PROMPT_ARG_VALUE_CHARS,
    SUMMARY_CODE_LINES,
    CanonicalTranslator,
    bounded_prompt_args,
)
from gaia.ui.turn_summary import TurnLedger


def _drain(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


# ── token usage ────────────────────────────────────────────────────────────


def test_final_usage_reaches_the_answer_event_as_the_in_process_path_names_it():
    handler = SSEOutputHandler()
    final = {
        "type": "final",
        "answer": "done",
        "usage": {
            "steps": 3,
            "tools_used": 2,
            "elapsed": 4.2,
            "tokens": 812,
            "input_tokens": 15000,
            "cached_tokens": 9000,
            "ttft": 0.9,
            "tok_per_s": 51.0,
            "unknown_field": "dropped",
        },
    }
    assert relay._dispatch_one(handler, final, GAIA_PROFILE) is True
    (answer,) = _drain(handler)
    assert answer["type"] == "answer" and answer["content"] == "done"
    assert answer["tokens"] == 812
    assert answer["input_tokens"] == 15000
    assert answer["cached_tokens"] == 9000
    assert answer["steps"] == 3
    assert "unknown_field" not in answer


def test_a_final_without_usage_still_answers():
    handler = SSEOutputHandler()
    relay._dispatch_one(handler, {"type": "final", "answer": "hi"}, GAIA_PROFILE)
    assert _drain(handler) == [{"type": "answer", "content": "hi"}]


def test_relayed_usage_becomes_the_messages_token_counts():
    answer = {"type": "answer", "content": "x", "tokens": 812, "input_tokens": 15000}
    stats = helpers._turn_inference_stats(
        {"input_tokens": 0, "output_tokens": 0}, answer
    )
    assert stats["output_tokens"] == 812 and stats["input_tokens"] == 15000


def test_a_relayed_fireworks_turn_is_priced_from_the_model_it_was_sent():
    handler = SimpleNamespace(turn_ledger=TurnLedger())
    answer = {"tokens": 1_000, "input_tokens": 100_000, "cached_tokens": 50_000}
    where = InferenceLocation(
        "fireworks", "accounts/fireworks/models/deepseek-v4p1-flash", remote=True
    )
    summary = helpers._turn_summary(handler, None, "q", answer, where)
    assert summary["cost"]["kind"] == "estimate"
    # 50k uncached at $0.30, 50k cached at $0.006, 1k out at $1.20 per 1M.
    assert summary["cost"]["usd"] == pytest.approx(0.0165, rel=1e-6)
    # No agent and no relayed location is still reported as unknown, not local.
    assert helpers._turn_summary(handler, None, "q", answer)["cost"] == {
        "kind": "unknown"
    }


# ── permission prompt arguments ────────────────────────────────────────────


def _permission(tool, args, **extra):
    t = CanonicalTranslator("r1", agent_id="gaia")
    (event,) = t.translate(
        {"type": "permission_request", "tool": tool, "args": args, **extra}
    )
    return event


def test_a_code_prompt_carries_all_of_the_code_its_summary_shortens():
    code = "\n".join(f"x{i} = {i}" for i in range(SUMMARY_CODE_LINES * 3))
    event = _permission("run_python", {"code": code}, confirm_id="c1")
    assert "more lines" in event["summary"]
    assert event["args"] == {"code": code}
    assert "args_truncated" not in event


def test_a_path_prompt_carries_the_path_and_its_kind():
    event = _permission(
        "allow_path_access",
        {"path": "C:\\Users\\me\\report.xlsx", "kind": "file"},
        tool_label="using a file",
    )
    assert event["args"] == {"path": "C:\\Users\\me\\report.xlsx", "kind": "file"}
    assert event["tool_label"] == "using a file"


def test_prompt_args_are_capped_and_say_so():
    huge = "a" * (PROMPT_ARG_VALUE_CHARS + 500)
    args, cut = bounded_prompt_args(
        {"code": huge, "n": 3, "flag": True, "opts": {"k": [1, 2]}, "none": None}
    )
    assert cut is True
    assert args["code"].startswith("a" * PROMPT_ARG_VALUE_CHARS)
    assert args["code"].endswith("[+500 more characters not shown]")
    assert args["n"] == 3 and args["flag"] is True and args["none"] is None
    assert args["opts"] == {"k": [1, 2]}
    event = _permission("run_python", {"code": huge})
    assert event["args_truncated"] is True


def test_prompt_args_are_made_json_safe():
    args, cut = bounded_prompt_args({"p": object.__new__(object)})
    assert cut is False and isinstance(args["p"], str)


def test_the_relay_hands_the_ui_prompt_its_args_and_label():
    handler = SSEOutputHandler()
    handler.set_relay_confirmation = lambda _cid, _scope=None: None
    relay._dispatch_one(
        handler,
        {
            "type": "needs_confirmation",
            "action": "allow_path_access",
            "summary": "Allow GAIA to use the file C:\\a.txt for this session?",
            "confirm_id": "c-9",
            "tool_label": "using a file",
            "args": {"path": "C:\\a.txt", "kind": "file"},
        },
        GAIA_PROFILE,
        answers_confirmations=True,
    )
    (prompt,) = _drain(handler)
    assert prompt["type"] == "permission_request"
    assert prompt["args"] == {"path": "C:\\a.txt", "kind": "file"}
    assert prompt["tool_label"] == "using a file"
    # No "always": folder access asks afresh every time.
    assert "always_scope" not in prompt


# ── memory mode ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "version, expected", [("2.18", True), ("3.0", True), ("2.17", False), (None, False)]
)
def test_only_a_flagship_at_2_18_takes_memory(version, expected):
    assert memory_supported(GAIA_PROFILE, version) is expected
    assert memory_supported(EMAIL_PROFILE, "9.9") is False


@pytest.mark.parametrize(
    "private, enabled, mode",
    [(True, True, "private"), (True, False, "private"), (False, False, "off")],
)
def test_memory_mode_follows_the_chat_and_settings(monkeypatch, private, enabled, mode):
    monkeypatch.setattr(helpers, "memory_enabled", lambda _db: enabled)
    assert helpers._memory_mode({"private": int(private)}, None) == mode


def test_memory_stays_on_by_default(monkeypatch):
    db = SimpleNamespace(get_setting=lambda _key, default=None: default)
    assert helpers._memory_mode({}, db) == "on"


class _Proxy:
    agent_id = "gaia"

    def __init__(self):
        self.bodies: list = []

    def query_stream(self, body, read_timeout, on_response):
        self.bodies.append(body)
        yield {"type": "final", "answer": "ok"}

    def cancel_query(self, _rid):
        pass


@pytest.mark.parametrize("memory", ["private", "off", "on"])
def test_the_relay_sends_the_memory_mode(memory):
    proxy = _Proxy()
    relay.relay_query(
        SSEOutputHandler(),
        proxy,
        query="q",
        context=[],
        profile=GAIA_PROFILE,
        memory=memory,
    )
    assert proxy.bodies[0]["memory"] == memory


def test_the_relay_omits_memory_it_was_not_given():
    proxy = _Proxy()
    relay.relay_query(
        SSEOutputHandler(), proxy, query="q", context=[], profile=GAIA_PROFILE
    )
    assert "memory" not in proxy.bodies[0]


class _Handler:
    def __init__(self):
        self.events: list = []
        self.cancelled = threading.Event()

    def _emit(self, event):
        self.events.append(event)


def _dispatch(monkeypatch, api_version, memory):
    handle = SimpleNamespace(
        api_version=api_version,
        proxy=lambda: SimpleNamespace(init=lambda: (200, {})),
    )
    monkeypatch.setattr(
        daemon_client_module, "acquire_handle", lambda _agent_id: handle
    )
    monkeypatch.setattr(helpers, "_replay_cloud_key_for_relay", lambda *_a: None)
    calls: list = []
    monkeypatch.setattr(relay, "relay_query", lambda *a, **k: calls.append(k))
    handler = _Handler()
    helpers._dispatch_sidecar_query(
        handler,
        ChatRequest(session_id="s1", message="hi", agent_type="gaia"),
        [],
        "m",
        "gaia",
        memory=memory,
    )
    return handler.events, calls


@pytest.mark.parametrize("memory", ["private", "off"])
def test_a_private_turn_is_stopped_before_an_agent_too_old_to_honour_it(
    monkeypatch, memory
):
    events, calls = _dispatch(monkeypatch, "2.17", memory)
    assert calls == []
    assert events == [
        {"type": "agent_error", "content": GAIA_PROFILE.memory_upgrade_message}
    ]


def test_a_memory_on_turn_still_runs_on_an_older_agent(monkeypatch):
    events, calls = _dispatch(monkeypatch, "2.17", "on")
    assert len(calls) == 1 and calls[0]["memory"] is None
    assert not [e for e in events if e["type"] == "agent_error"]


def test_a_current_agent_is_told_the_mode(monkeypatch):
    _events, calls = _dispatch(monkeypatch, "2.18", "private")
    assert calls[0]["memory"] == "private"


# ── automatic memory writes ────────────────────────────────────────────────


class _Store:
    def __init__(self):
        self.writes: list = []

    def store(self, **kw):
        self.writes.append(("store", kw))
        return "k1"

    def delete(self, kid):
        self.writes.append(("delete", kid))

    def update_confidence(self, *a, **kw):
        self.writes.append(("confidence", a))

    def get_by_category(self, *a, **kw):
        return [{"id": "e1", "content": "run_python: boom", "metadata": {}}]


class _Host(MemoryMixin):
    def __init__(self, incognito):
        self._memory_store = _Store()
        self._memory_context = "global"
        self._incognito = incognito

    def _embed_and_index(self, *_a, **_kw):
        pass

    def _workspace_lessons(self, limit=0):
        return [{"id": "l1", "metadata": {"operation": "x", "fix": "y"}}]


def test_no_automatic_writer_touches_memory_in_a_private_chat():
    host = _Host(incognito=True)
    host._auto_store_error("run_python", "NameError: x is not defined")
    host._forget_errors_for_operation("run_python", {"code": "x"})
    host._learn_from_success("run_python", {"code": "x"})
    host._confirm_lesson({"id": "l1"})
    host._forget_lesson("l1")
    assert host._memory_store.writes == []


def test_automatic_errors_are_still_saved_with_memory_on():
    host = _Host(incognito=False)
    host._auto_store_error("run_python", "NameError: x is not defined")
    assert [w[0] for w in host._memory_store.writes] == ["store"]
