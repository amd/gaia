# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""EmailProxyAgent: the sidecar-backed chat agent (#1767 cutover).

These tests isolate the proxy agent's tool layer from the LLM loop by stubbing
the base ``Agent.__init__`` (which would otherwise reach Lemonade) down to tool
registration. The contract that matters here is: each tool forwards to the
sidecar proxy and the ``pre_scan_inbox`` tool returns the EXACT
``{"ok": true, "data": {"kind": "email_pre_scan", …}}`` envelope the SSE card
pipeline depends on.
"""

import json

import pytest

import gaia.agents.base.agent as agentmod
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.ui.email_sidecar import proxy_agent as pa
from gaia.ui.email_sidecar.errors import SidecarHTTPError


class _FakeProxy:
    def __init__(self):
        self.calls = []

    def pre_scan_inbox(self, payload):
        self.calls.append(("prescan", payload))
        return {
            "result": {
                "kind": "email_pre_scan",
                "urgent": [],
                "actionable": [],
                "informational_count": 0,
                "suggested_archives": [],
            }
        }

    def search_inbox(self, payload):
        self.calls.append(("search", payload))
        return {"query": payload.get("query"), "count": 0, "messages": []}

    def confirm(self, payload):
        self.calls.append(("confirm", payload))
        return {"confirmation_token": "tok-1", "action": payload["action"]}

    def archive(self, payload):
        self.calls.append(("archive", payload))
        return {"message_id": payload["message_id"], "batch_id": "b1"}

    def unarchive(self, payload):
        self.calls.append(("unarchive", payload))
        return {"batch_id": payload["batch_id"], "restored": 1}

    def calendar_events(self, params):
        self.calls.append(("cal", params))
        return {"events": []}


class _FakeManager:
    def __init__(self, *, proxy=None, http_error=None):
        self._proxy = proxy or _FakeProxy()
        self._http_error = http_error
        self._running = False
        self.starts = 0

    @property
    def is_running(self):
        return self._running

    def start(self):
        self.starts += 1
        self._running = True
        return "http://127.0.0.1:9"

    def proxy(self, **_kw):
        if self._http_error:
            raise self._http_error
        return self._proxy


@pytest.fixture
def stub_agent_init(monkeypatch):
    """Stub base Agent.__init__ so construction registers tools without an LLM."""

    def _fake_init(self, **kwargs):
        self.model_id = kwargs.get("model_id")
        self.conversation_history = []
        self._register_tools()

    monkeypatch.setattr(agentmod.Agent, "__init__", _fake_init)


def _make(manager, **kw):
    return pa.EmailProxyAgent(model_id="Gemma-4-E4B-it-GGUF", manager=manager, **kw)


def test_registers_only_sidecar_mapped_tools(stub_agent_init):
    _make(_FakeManager())
    assert sorted(_TOOL_REGISTRY.keys()) == [
        "archive_message",
        "list_calendar_events",
        "pre_scan_inbox",
        "search_messages",
        "undo_archive_batch",
    ]


def test_pre_scan_returns_email_pre_scan_card_envelope(stub_agent_init):
    # THE end-to-end correctness property: the wire shape the SSE handler injects.
    mgr = _FakeManager()
    _make(mgr)
    out = json.loads(_TOOL_REGISTRY["pre_scan_inbox"]["function"](max_messages=10))
    assert out["ok"] is True
    assert out["data"]["kind"] == "email_pre_scan"
    assert mgr.starts == 1  # lazily started the sidecar
    assert mgr._proxy.calls[0] == ("prescan", {"max_messages": 10})


def test_pre_scan_clamps_max_messages(stub_agent_init):
    mgr = _FakeManager()
    _make(mgr)
    _TOOL_REGISTRY["pre_scan_inbox"]["function"](max_messages=9999)
    assert mgr._proxy.calls[0][1]["max_messages"] == 100


def test_search_forwards_query(stub_agent_init):
    mgr = _FakeManager()
    _make(mgr)
    out = json.loads(
        _TOOL_REGISTRY["search_messages"]["function"](query="is:unread", max_results=5)
    )
    assert out["ok"] is True
    assert mgr._proxy.calls[0] == ("search", {"query": "is:unread", "max_results": 5})


def test_archive_mints_token_then_archives_with_provider(stub_agent_init):
    # mail_provider must flow to the REST provider field on confirm + archive.
    mgr = _FakeManager()
    _make(mgr, mail_provider="google")
    out = json.loads(_TOOL_REGISTRY["archive_message"]["function"](message_id="m1"))
    assert out["ok"] is True
    confirm_call, archive_call = mgr._proxy.calls
    assert confirm_call == (
        "confirm",
        {"action": "archive", "message_id": "m1", "provider": "google"},
    )
    assert archive_call[1]["confirmation_token"] == "tok-1"
    assert archive_call[1]["provider"] == "google"


def test_explicit_mailbox_overrides_session_provider(stub_agent_init):
    mgr = _FakeManager()
    _make(mgr, mail_provider="google")
    _TOOL_REGISTRY["archive_message"]["function"](message_id="m1", mailbox="microsoft")
    assert mgr._proxy.calls[0][1]["provider"] == "microsoft"


def test_calendar_events_plumbs_provider(stub_agent_init):
    mgr = _FakeManager()
    _make(mgr, mail_provider="microsoft")
    json.loads(
        _TOOL_REGISTRY["list_calendar_events"]["function"](time_min="2026-06-30")
    )
    assert mgr._proxy.calls[0] == (
        "cal",
        {"time_min": "2026-06-30", "provider": "microsoft"},
    )


def test_sidecar_http_error_becomes_loud_error_envelope(stub_agent_init):
    # A sidecar 502 (e.g. Lemonade down) surfaces as ok:false with the actionable
    # detail — never a silent empty card.
    err = SidecarHTTPError(502, "local LLM triage failed: Lemonade not reachable")
    mgr = _FakeManager(http_error=err)
    _make(mgr)
    out = json.loads(_TOOL_REGISTRY["pre_scan_inbox"]["function"]())
    assert out["ok"] is False
    assert "Lemonade not reachable" in out["error"]


def test_pre_scan_envelope_triggers_real_sse_card_injection(stub_agent_init):
    # End-to-end card property WITHOUT a live mailbox: feed the proxy agent's
    # actual pre_scan_inbox output through the REAL SSE handler and prove it
    # injects an ``email_pre_scan`` render payload. This is the seam the renderer
    # depends on (sse_handler.py: pre_scan_inbox -> email_pre_scan).
    from gaia.ui.sse_handler import SSEOutputHandler

    mgr = _FakeManager()
    _make(mgr)
    envelope_str = _TOOL_REGISTRY["pre_scan_inbox"]["function"](max_messages=5)

    handler = SSEOutputHandler()
    handler._last_tool_name = "pre_scan_inbox"
    handler._capture_render_payload(envelope_str)

    assert len(handler._pending_render_payloads) == 1
    lang, inner = handler._pending_render_payloads[0]
    assert lang == "email_pre_scan"
    assert inner["kind"] == "email_pre_scan"
