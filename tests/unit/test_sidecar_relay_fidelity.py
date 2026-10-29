# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a relayed flagship turn keeps on its way to the Agent UI.

The Electron app relays every chat to the flagship sidecar. The relay used to
drop three things the in-process path keeps: the turn's token usage (so a
Fireworks chat showed no tokens and no cost), a permission prompt's arguments
(so code was approved from a shortened summary and a path prompt said an
existing file "doesn't exist yet"), and the chat's private / memory-off state
(so the agent saved memories from a private chat). It also skipped the turn
ledger, so a file the agent wrote never reached the chat's Outputs.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from gaia.agents.base.call_risk import RISK_NOTE_ARG
from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.tool_grants import SCRATCH_ARG
from gaia.llm.inference_location import InferenceLocation
from gaia.ui import _chat_helpers as helpers
from gaia.ui.email_sidecar import daemon_client as daemon_client_module
from gaia.ui.email_sidecar import relay
from gaia.ui.email_sidecar.profiles import (
    EMAIL_PROFILE,
    GAIA_PROFILE,
    SIDECAR_AGENT_IDS,
    memory_supported,
    profile_for,
    surface_supported,
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
    handler.set_relay_confirmation = lambda _cid: None
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
    # Still no "always": the grant would live where this chat can't see it.
    assert "always_scope" not in prompt


def test_prompt_only_keys_never_reach_the_wire_args():
    event = _permission(
        "run_shell_command",
        {
            "command": "git push",
            RISK_NOTE_ARG: "Sends your commits to the remote repository.",
            SCRATCH_ARG: "C:\\scratch",
        },
    )
    assert event["args"] == {"command": "git push"}
    assert event["prompt_context"] == {
        "risk_note": "Sends your commits to the remote repository."
    }


def test_a_folder_gaia_added_is_context_not_an_argument():
    event = _permission(
        "run_shell_command",
        {"command": "git status", "working_directory": "C:\\repo"},
        call_args={"command": "git status"},
    )
    assert event["args"] == {"command": "git status"}
    assert event["prompt_context"] == {"working_directory": "C:\\repo"}

    sent = _permission(
        "run_shell_command",
        {"command": "ls", "working_directory": "C:\\repo"},
        call_args={"command": "ls", "working_directory": "C:\\repo"},
    )
    assert sent["args"] == {"command": "ls", "working_directory": "C:\\repo"}
    assert "prompt_context" not in sent


def test_without_a_noted_call_the_folder_is_the_models():
    # Only Agent._execute_tool adds a folder, and it always notes the call
    # first; every other prompt passes the model's arguments through as-is.
    event = _permission(
        "run_shell_command", {"command": "ls", "working_directory": "C:\\repo"}
    )
    assert event["args"] == {"command": "ls", "working_directory": "C:\\repo"}
    assert "prompt_context" not in event


def test_a_noted_call_is_used_once_and_only_for_its_tool():
    handler = SSEOutputHandler()
    handler.note_call_args("run_shell_command", {"command": "ls"})
    handler.confirm_tool_execution("run_python", {"code": "1"}, timeout=0.01)
    handler.confirm_tool_execution(
        "run_shell_command",
        {"command": "ls", "working_directory": "C:\\r"},
        timeout=0.01,
    )
    prompts = [e for e in _drain(handler) if e["type"] == "permission_request"]
    assert [("call_args" in p) for p in prompts] == [False, False]

    handler.note_call_args("run_shell_command", {"command": "ls"})
    handler.confirm_tool_execution(
        "run_shell_command",
        {"command": "ls", "working_directory": "C:\\r"},
        timeout=0.01,
    )
    [prompt] = [e for e in _drain(handler) if e["type"] == "permission_request"]
    assert prompt["call_args"] == {"command": "ls"}


def test_a_noted_call_matching_the_prompt_is_not_sent_twice():
    handler = SSEOutputHandler()
    code = {"file_path": "C:\\a.py", "content": "x" * 10_000}
    handler.note_call_args("write_file", code)
    handler.confirm_tool_execution("write_file", dict(code), timeout=0.01)
    [prompt] = [e for e in _drain(handler) if e["type"] == "permission_request"]
    assert "call_args" not in prompt
    [wire] = CanonicalTranslator("r1", agent_id="gaia").translate(prompt)
    assert wire["args"] == code


def test_the_relay_puts_gaias_context_back_on_the_ui_prompt():
    handler = SSEOutputHandler()
    handler.set_relay_confirmation = lambda _cid: None
    relay._dispatch_one(
        handler,
        {
            "type": "needs_confirmation",
            "action": "run_shell_command",
            "summary": "git push\nin C:\\repo\nSends your commits.",
            "confirm_id": "c-1",
            "args": {"command": "git push"},
            "prompt_context": {
                "working_directory": "C:\\repo",
                "risk_note": "Sends your commits.",
            },
        },
        GAIA_PROFILE,
        answers_confirmations=True,
    )
    (prompt,) = _drain(handler)
    assert prompt["args"] == {
        "command": "git push",
        "working_directory": "C:\\repo",
        RISK_NOTE_ARG: "Sends your commits.",
    }


def test_a_resolved_write_path_is_context_not_an_argument():
    """rc11: a relayed "Allow writing file?" still named a bare shop2.txt."""
    event = _permission(
        "write_file",
        {"file_path": "/Users/you/shop2.txt", "content": "milk"},
        call_args={"file_path": "shop2.txt", "content": "milk"},
    )
    assert event["args"] == {"file_path": "shop2.txt", "content": "milk"}
    assert event["prompt_context"] == {"file_path": "/Users/you/shop2.txt"}

    same = _permission(
        "write_file",
        {"file_path": "/Users/you/a.txt", "content": "x"},
        call_args={"file_path": "/Users/you/a.txt", "content": "x"},
    )
    assert "prompt_context" not in same


def test_the_relay_shows_the_ui_prompt_where_a_write_lands():
    handler = SSEOutputHandler()
    handler.set_relay_confirmation = lambda _cid: None
    relay._dispatch_one(
        handler,
        {
            "type": "needs_confirmation",
            "action": "write_file",
            "summary": "shop2.txt",
            "confirm_id": "c-2",
            "args": {"file_path": "shop2.txt", "content": "milk"},
            "prompt_context": {"file_path": "/Users/you/shop2.txt"},
        },
        GAIA_PROFILE,
        answers_confirmations=True,
    )
    (prompt,) = _drain(handler)
    assert prompt["args"] == {"file_path": "/Users/you/shop2.txt", "content": "milk"}


def test_a_relative_write_reaches_the_ui_prompt_as_its_full_path():
    """End to end: the agent's prompt, the sidecar wire, then the UI relay."""
    agent_side = SSEOutputHandler()
    agent_side.note_call_args("write_file", {"file_path": "shop2.txt", "content": "m"})
    agent_side.confirm_tool_execution(
        "write_file",
        {"file_path": "/Users/you/shop2.txt", "content": "m"},
        timeout=0.01,
    )
    [prompt] = [e for e in _drain(agent_side) if e["type"] == "permission_request"]
    [wire] = CanonicalTranslator("r1", agent_id="gaia").translate(prompt)
    wire["confirm_id"] = "c-3"

    ui_side = SSEOutputHandler()
    ui_side.set_relay_confirmation = lambda _cid: None
    relay._dispatch_one(ui_side, wire, GAIA_PROFILE, answers_confirmations=True)
    (shown,) = _drain(ui_side)
    assert wire["args"]["file_path"] == "shop2.txt"
    assert shown["args"]["file_path"] == "/Users/you/shop2.txt"


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
        self.turn_ledger = TurnLedger()

    def _emit(self, event):
        self.events.append(event)


def _dispatch(monkeypatch, api_version, memory, agent_type="gaia", proxy=None):
    handle = SimpleNamespace(
        api_version=api_version,
        proxy=lambda: proxy or SimpleNamespace(init=lambda: (200, {})),
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
        ChatRequest(session_id="s1", message="hi", agent_type=agent_type),
        [],
        "m",
        agent_type,
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


def test_every_profile_without_a_memory_floor_explains_the_refusal():
    for agent_id in SIDECAR_AGENT_IDS:
        profile = profile_for(agent_id)
        if profile.min_memory_api_version is None:
            assert profile.memory_upgrade_message.strip(), agent_id


@pytest.mark.parametrize("version", ["2.4", "2.18", "9.9"])
@pytest.mark.parametrize("memory", ["private", "off"])
def test_email_refuses_private_turns_before_init_or_query(monkeypatch, version, memory):
    init_calls = []
    proxy = SimpleNamespace(init=lambda: (init_calls.append(True) or (200, {})))
    events, calls = _dispatch(monkeypatch, version, memory, "email", proxy)

    assert init_calls == []
    assert calls == []
    assert len(events) == 1 and events[0]["type"] == "agent_error"
    message = events[0]["content"]
    assert "email agent" in message
    assert "private" in message and "memory" in message
    assert "not sent" in message


@pytest.mark.parametrize("version", ["2.4", "2.18", "9.9"])
def test_email_with_memory_on_still_runs_without_an_unsupported_field(
    monkeypatch, version
):
    init_calls = []
    proxy = SimpleNamespace(init=lambda: (init_calls.append(True) or (200, {})))
    events, calls = _dispatch(monkeypatch, version, "on", "email", proxy)

    assert init_calls == [True]
    assert len(calls) == 1 and calls[0]["memory"] is None
    assert not [event for event in events if event["type"] == "agent_error"]


# ── provider-error wording (#5295) ─────────────────────────────────────────


@pytest.mark.parametrize(
    "version, expected", [("2.20", True), ("3.0", True), ("2.19", False), (None, False)]
)
def test_only_a_flagship_at_2_20_takes_the_surface(version, expected):
    assert surface_supported(GAIA_PROFILE, version) is expected
    assert surface_supported(EMAIL_PROFILE, "9.9") is False


@pytest.mark.parametrize("version, surface", [("2.20", "agent_ui"), ("2.19", None)])
def test_the_agent_ui_names_itself_to_an_agent_that_takes_it(
    monkeypatch, version, surface
):
    _events, calls = _dispatch(monkeypatch, version, "on")
    assert calls[0]["surface"] == surface


def test_the_relay_sends_the_surface_only_when_given():
    proxy = _Proxy()
    relay.relay_query(
        SSEOutputHandler(),
        proxy,
        query="q",
        context=[],
        profile=GAIA_PROFILE,
        surface="agent_ui",
    )
    relay.relay_query(
        SSEOutputHandler(), proxy, query="q", context=[], profile=GAIA_PROFILE
    )
    assert proxy.bodies[0]["surface"] == "agent_ui"
    assert "surface" not in proxy.bodies[1]


# ── what the turn made ─────────────────────────────────────────────────────


def _relayed_summary(tool, args, result):
    """The turn summary a relayed tool call yields, from the sidecar's own events."""
    sidecar = SSEOutputHandler()
    sidecar.print_tool_usage(tool)
    sidecar.pretty_print_json(args, "Arguments")
    sidecar.pretty_print_json(result)
    sidecar.print_tool_complete()
    translator = CanonicalTranslator("r1", agent_id="gaia")
    canonical = [c for e in _drain(sidecar) for c in translator.translate(e)]
    ui = SSEOutputHandler()
    for event in canonical + translator.flush():
        relay._dispatch_one(ui, event, GAIA_PROFILE)
    ledger = TurnLedger()
    for event in _drain(ui):
        if event["type"] == "tool_start":
            ledger.tool_started(event["tool"])
        elif event["type"] == "tool_args":
            ledger.tool_args(event["args"])
        elif event["type"] == "tool_result":
            ledger.tool_result(event["data"], event["success"])
    return ledger.summary(location=None)


def _relayed_outputs(tool, args, result):
    """The Outputs a relayed tool call adds."""
    return _relayed_summary(tool, args, result)["outputs"]


_FACT = "my locker combination hint is heron-plum-604"


def test_a_memory_a_private_chat_refused_to_save_is_not_listed():
    outputs = _relayed_outputs(
        "remember",
        {"fact": _FACT},
        {
            "status": "skipped",
            "message": "This is a private chat, so nothing is saved to memory.",
        },
    )
    assert outputs == []


def test_a_memory_the_relayed_turn_saved_is_listed():
    outputs = _relayed_outputs(
        "remember",
        {"fact": _FACT},
        {"status": "stored", "stored": 1, "knowledge_id": "k1", "message": "ok"},
    )
    assert [(o["kind"], o["name"]) for o in outputs] == [("memory", _FACT)]


def test_a_relayed_call_the_user_denied_contacted_nothing():
    summary = _relayed_summary(
        "fetch_page",
        {"url": "https://example.com/page"},
        {"status": "denied", "message": "The user declined this action."},
    )
    assert summary["sites"] == []
    assert summary["live_data"] == {"attempted": 0, "failed": 0}


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


# ── the turn summary and tool timer ────────────────────────────────────────


def _sidecar_events(tool, args, result):
    """The canonical events a flagship sidecar sends for one tool call: its
    own handler's events run through the same translator its /query uses."""
    agent_side = SSEOutputHandler()
    agent_side.print_tool_usage(tool)
    agent_side.pretty_print_json(args, title="Arguments")
    agent_side.pretty_print_json(result)
    agent_side.print_tool_complete()
    translator = CanonicalTranslator("run-1", agent_id="gaia")
    canonical = []
    for event in _drain(agent_side):
        canonical.extend(translator.translate(event))
    canonical.extend(translator.flush())
    assert [e["type"] for e in canonical] == ["tool_call", "tool_result"]
    return canonical


def _relay(handler, events):
    for event in events:
        relay._dispatch_one(handler, event, GAIA_PROFILE)


def test_a_relayed_page_fetch_names_its_site_in_the_turn_summary():
    handler = SSEOutputHandler()
    for url in ("https://www.example.com/a", "https://docs.python.org/3/"):
        _relay(
            handler,
            _sidecar_events(
                "fetch_page", {"url": url}, {"status": "success", "url": url}
            ),
        )
    summary = handler.turn_ledger.summary(location=None)
    assert summary["sites"] == ["example.com", "docs.python.org"]
    assert summary["live_data"] == {"attempted": 2, "failed": 0}


_SEARCH_RESULT = (
    'Web search results for: "2024 nobel physics"\n\n'
    "1. The Nobel Prize in Physics 2024 - NobelPrize.org\n"
    "   https://www.nobelprize.org/prizes/physics/2024/summary/\n"
    "   The Nobel Prize in Physics 2024 was awarded jointly to John J. Hopfield"
    " and Geoffrey Hinton for foundational discoveries and inventions.\n\n"
    "2. Hopfield and Hinton win physics Nobel - Reuters\n"
    "   https://www.reuters.com/science/nobel-physics-2024/\n"
    "   The two scientists were honoured for work that enables machine learning"
    " with artificial neural networks.\n\n"
    "Use fetch_page(url) to read the full content of any result."
)


def test_a_relayed_web_search_lists_its_hits_as_sources_and_names_its_site():
    handler = SSEOutputHandler()
    _relay(
        handler,
        _sidecar_events("search_web", {"query": "2024 nobel physics"}, _SEARCH_RESULT),
    )
    summary = handler.turn_ledger.summary(location=None)
    assert summary["pages"] == [
        {
            "url": "https://www.nobelprize.org/prizes/physics/2024/summary/",
            "title": "The Nobel Prize in Physics 2024 - NobelPrize.org",
            "kind": "search_result",
        },
        {
            "url": "https://www.reuters.com/science/nobel-physics-2024/",
            "title": "Hopfield and Hinton win physics Nobel - Reuters",
            "kind": "search_result",
        },
    ]
    assert summary["sites"] == ["duckduckgo.com"]
    assert summary["live_data"] == {"attempted": 1, "failed": 0}


def _real_search_web(hits):
    """What the built-in ``search_web`` tool returns for *hits*, from the tool
    itself with only DuckDuckGo stubbed."""
    from unittest.mock import MagicMock, patch

    from gaia.agents.tools.browser_tools import BrowserToolsMixin

    tools = {}

    class _Host(BrowserToolsMixin):
        def __init__(self, web_client):
            self._web_client = web_client
            self._path_validator = None
            self._tools = {}

    def capture(atomic=True):
        def decorator(func):
            tools[func.__name__] = func
            return func

        return decorator

    client = MagicMock()
    client.search_duckduckgo.return_value = hits
    with patch("gaia.agents.base.tools.tool", capture):
        _Host(client).register_browser_tools()
    return tools["search_web"]("q", num_results=10)


def _relayed_search(result):
    handler = SSEOutputHandler()
    _relay(handler, _sidecar_events("search_web", {"query": "q"}, result))
    return handler.turn_ledger.summary(location=None)


def test_the_real_search_tool_output_reaches_a_relayed_turn_as_sources():
    hits = [
        {
            "title": f"Result {i}",
            "url": f"https://site{i}.example.org/p",
            "snippet": "s",
        }
        for i in range(10)
    ]
    summary = _relayed_search(_real_search_web(hits))
    assert [p["url"] for p in summary["pages"]] == [h["url"] for h in hits]
    assert [p["title"] for p in summary["pages"]] == [h["title"] for h in hits]
    assert summary["sites"] == ["duckduckgo.com"]


def test_a_relayed_search_with_no_results_lists_no_sources():
    summary = _relayed_search(_real_search_web([]))
    assert summary["pages"] == []
    assert summary["live_data"] == {"attempted": 1, "failed": 0}


def test_a_relayed_search_lists_a_repeated_hit_once():
    hit = {"title": "Same", "url": "https://dup.example.org/", "snippet": ""}
    summary = _relayed_search(_real_search_web([hit, dict(hit)]))
    assert summary["pages"] == [
        {"url": "https://dup.example.org/", "title": "Same", "kind": "search_result"}
    ]


def test_a_relayed_search_hit_that_is_not_a_web_link_is_dropped():
    events = _sidecar_events("search_web", {"query": "q"}, _SEARCH_RESULT)
    events[-1]["data"]["sources"] = [
        {"title": "script", "url": "javascript:alert(1)"},
        {"title": "file", "url": "file:///C:/Windows/win.ini"},
        {"title": "no url"},
        {"title": "not a string", "url": ["https://a.example.org/"]},
        "https://bare-string.example.org/",
        {"title": {"x": 1}, "url": "https://ok.example.org/"},
    ]
    handler = SSEOutputHandler()
    _relay(handler, events)
    assert handler.turn_ledger.summary(location=None)["pages"] == [
        {"url": "https://ok.example.org/", "title": None, "kind": "search_result"}
    ]


def test_search_shaped_text_from_another_tool_is_not_a_source():
    summary = _relayed_summary("read_file", {"path": "notes.txt"}, _SEARCH_RESULT)
    assert all(p["kind"] != "search_result" for p in summary["pages"])
    assert summary["sites"] == []


def test_a_relayed_search_the_service_refused_names_no_sources():
    refused = {"status": "error", "error": "Web search is temporarily unavailable"}
    summary = _relayed_search(refused)
    assert summary["pages"] == []
    assert summary["sites"] == []


def test_a_relayed_write_lists_the_file_in_the_turn_summary(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("hi", encoding="utf-8")
    handler = SSEOutputHandler()
    _relay(
        handler,
        _sidecar_events(
            "write_file",
            {"file_path": str(path), "content": "hi"},
            {"status": "success", "file_path": str(path)},
        ),
    )
    files = handler.turn_ledger.summary(location=None)["files"]
    assert [f["path"] for f in files] == [str(path)]


def test_a_failed_relayed_write_lists_no_file(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("hi", encoding="utf-8")
    handler = SSEOutputHandler()
    _relay(
        handler,
        _sidecar_events(
            "write_file",
            {"file_path": str(path), "content": "hi"},
            {"status": "error", "error": "disk full"},
        ),
    )
    assert handler.turn_ledger.summary(location=None)["files"] == []


def test_a_relayed_tool_counts_as_running_until_its_result():
    handler = SSEOutputHandler()
    call, result = _sidecar_events(  # pylint: disable=unbalanced-tuple-unpacking
        "fetch_page", {"url": "https://example.com"}, {"status": "success"}
    )
    assert not handler.tool_running()
    relay._dispatch_one(handler, call, GAIA_PROFILE)
    assert handler.tool_running()
    relay._dispatch_one(handler, result, GAIA_PROFILE)
    assert not handler.tool_running()


# ── files the turn wrote ───────────────────────────────────────────────────


def _sidecar_write(tool, args, result):
    """What the sidecar streams for one tool call, as canonical events."""
    sidecar = SSEOutputHandler()
    sidecar.print_tool_usage(tool)
    sidecar.pretty_print_json(args, "Arguments")
    sidecar.pretty_print_json(result)
    sidecar.print_tool_complete()
    translator = CanonicalTranslator("r1", agent_id="gaia")
    events = [c for e in _drain(sidecar) for c in translator.translate(e)]
    return events + translator.flush()


def _relayed_files(events):
    ui = SSEOutputHandler()
    for event in events:
        relay._dispatch_one(ui, event, GAIA_PROFILE)
    return [f["path"] for f in helpers._turn_summary(ui, None, "q", None)["files"]]


def test_a_relayed_write_reaches_the_turn_summary(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    events = _sidecar_write(
        "write_file",
        {"file_path": str(target), "content": "hello"},
        {"status": "success", "file_path": str(target), "bytes_written": 5},
    )
    assert _relayed_files(events) == [str(target)]


def test_the_resolved_path_is_kept_when_the_agent_named_a_relative_one(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    events = _sidecar_write(
        "write_file",
        {"file_path": "notes.txt", "content": "hello"},
        {"status": "success", "file_path": str(target)},
    )
    (result,) = [e for e in events if e["type"] == "tool_result"]
    assert result["data"]["file_path"] == str(target)
    assert _relayed_files(events) == [str(target)]


def test_a_sidecar_that_sends_no_resolved_path_still_reports_an_absolute_one(
    tmp_path,
):
    # A sidecar built before the summary view kept the path sends only these.
    target = tmp_path / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    events = [
        {"type": "tool_call", "tool": "write_file", "args": {"file_path": str(target)}},
        {
            "type": "tool_result",
            "tool": "write_file",
            "data": {"summary": "Wrote notes.txt", "success": True},
        },
    ]
    assert _relayed_files(events) == [str(target)]


def test_a_failed_relayed_write_is_not_listed(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("old", encoding="utf-8")
    events = _sidecar_write(
        "write_file",
        {"file_path": str(target), "content": "hello"},
        {"status": "error", "error": "Permission denied", "file_path": str(target)},
    )
    assert _relayed_files(events) == []
