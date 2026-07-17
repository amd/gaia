# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the email streaming query dispatch helpers in
``gaia.ui._chat_helpers`` (#2109):

- ``_email_query_version_supported``: the sidecar contract-version floor gate
- ``_query_context_from_history``: history-pair -> /query context flattening
- ``_dispatch_email_query``: the streaming producer's self-contained email
  dispatch branch — every path either relays the sidecar's /query loop to
  completion or emits a terminal SSE error and returns.

After the daemon-client cutover (#2142 T3), ``_dispatch_email_query`` no
longer owns a spawning ``EmailSidecarManager`` — it acquires a
``SidecarHandle`` from the daemon via
``gaia.ui.email_sidecar.daemon_client.acquire_handle()`` and forwards through
the handle's bound proxy.
"""

import threading

import pytest

import gaia.ui.email_sidecar.daemon_client as daemon_client_module
import gaia.ui.email_sidecar.relay as relay_module
from gaia.ui._chat_helpers import (
    _dispatch_email_query,
    _email_query_version_supported,
    _query_context_from_history,
)
from gaia.ui.email_sidecar.errors import SidecarError
from gaia.ui.email_sidecar.relay import EMAIL_QUERY_VERSION_UPGRADE_MESSAGE
from gaia.ui.models import ChatRequest

# ── _email_query_version_supported ──────────────────────────────────────────


class TestEmailQueryVersionSupported:
    """Pins the MAJOR.MINOR floor (2.4) that gates the /query relay."""

    def test_none_is_unsupported(self):
        assert _email_query_version_supported(None) is False

    def test_empty_string_is_unsupported(self):
        assert _email_query_version_supported("") is False

    def test_exact_floor_is_supported(self):
        assert _email_query_version_supported("2.4") is True

    def test_above_floor_minor_is_supported(self):
        assert _email_query_version_supported("2.5") is True

    def test_above_floor_major_is_supported(self):
        assert _email_query_version_supported("3.0") is True

    def test_below_floor_minor_is_unsupported(self):
        assert _email_query_version_supported("2.3") is False

    def test_below_floor_major_is_unsupported(self):
        assert _email_query_version_supported("1.9") is False

    def test_major_only_no_minor_defaults_to_zero_and_is_unsupported(self):
        """ "2" parses as (2, 0), which is below the (2, 4) floor."""
        assert _email_query_version_supported("2") is False

    def test_malformed_string_is_unsupported_no_crash(self):
        assert _email_query_version_supported("abc") is False


# ── _query_context_from_history ─────────────────────────────────────────────


class TestQueryContextFromHistory:
    """Pins the truncation/windowing the /query relay applies to history."""

    def test_empty_history_returns_empty_list(self):
        assert _query_context_from_history([]) == []

    def test_two_pairs_produce_four_alternating_dicts(self):
        pairs = [("Q1", "A1"), ("Q2", "A2")]
        result = _query_context_from_history(pairs)
        assert result == [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]

    def test_more_than_five_pairs_keeps_only_last_five(self):
        pairs = [(f"Q{i}", f"A{i}") for i in range(8)]
        result = _query_context_from_history(pairs)
        assert len(result) == 10  # 5 pairs * 2 messages
        assert result[0] == {"role": "user", "content": "Q3"}
        assert result[-1] == {"role": "assistant", "content": "A7"}

    def test_long_assistant_message_is_truncated_with_marker(self):
        long_answer = "x" * 3000
        pairs = [("short question", long_answer)]
        result = _query_context_from_history(pairs)
        assistant_entry = result[1]
        assert assistant_entry["content"].endswith("... (truncated)")
        assert len(assistant_entry["content"]) == 2000 + len("... (truncated)")

    def test_short_assistant_message_is_not_truncated(self):
        pairs = [("Q", "short answer")]
        result = _query_context_from_history(pairs)
        assert result[1]["content"] == "short answer"

    def test_long_user_message_is_truncated_without_marker(self):
        """User messages are truncated to 2000 chars but never get the
        '... (truncated)' marker — only assistant messages do (mirrors the
        asymmetric truncation already used elsewhere in this module)."""
        long_question = "y" * 3000
        pairs = [(long_question, "ok")]
        result = _query_context_from_history(pairs)
        user_entry = result[0]
        assert len(user_entry["content"]) == 2000
        assert not user_entry["content"].endswith("... (truncated)")


# ── _dispatch_email_query ────────────────────────────────────────────────────


class _FakeSSEHandler:
    """Minimal stand-in for ``SSEOutputHandler`` — only the attributes
    ``_dispatch_email_query`` touches."""

    def __init__(self):
        self.events = []
        self.cancelled = threading.Event()

    def _emit(self, event):
        self.events.append(event)


class _FakeProxy:
    def __init__(self, init_result=(200, {}), init_error=None):
        self._init_result = init_result
        self._init_error = init_error
        self.init_called = False

    def init(self):
        self.init_called = True
        if self._init_error is not None:
            raise self._init_error
        return self._init_result


class _FakeHandle:
    def __init__(
        self,
        api_version="2.4",
        init_result=(200, {}),
        init_error=None,
    ):
        self.api_version = api_version
        self.proxy_called = False
        self._proxy = _FakeProxy(init_result=init_result, init_error=init_error)

    def proxy(self):
        self.proxy_called = True
        return self._proxy


def _make_request(message="hi"):
    return ChatRequest(session_id="s1", message=message, agent_type="email")


class TestDispatchEmailQuery:
    """Every path in ``_dispatch_email_query`` either relays to completion
    or emits exactly one terminal ``agent_error`` and returns — never lets
    an exception escape, and never calls ``relay_query`` once a pre-flight
    check has already failed."""

    def test_acquire_failure_emits_agent_error_never_calls_relay(self, monkeypatch):
        def _acquire(agent_id="email"):
            raise SidecarError("boom")

        monkeypatch.setattr(daemon_client_module, "acquire_handle", _acquire)
        relay_calls = []
        monkeypatch.setattr(
            relay_module,
            "relay_query",
            lambda *a, **k: relay_calls.append((a, k)),
        )

        handler = _FakeSSEHandler()
        _dispatch_email_query(handler, _make_request(), [], "some-model")

        assert len(handler.events) == 1
        assert handler.events[0] == {"type": "agent_error", "content": "boom"}
        assert relay_calls == []

    def test_version_below_floor_short_circuits_before_proxy(self, monkeypatch):
        fake_handle = _FakeHandle(api_version="2.3")
        monkeypatch.setattr(
            daemon_client_module, "acquire_handle", lambda agent_id="email": fake_handle
        )
        relay_calls = []
        monkeypatch.setattr(
            relay_module,
            "relay_query",
            lambda *a, **k: relay_calls.append((a, k)),
        )

        handler = _FakeSSEHandler()
        _dispatch_email_query(handler, _make_request(), [], "model")

        assert fake_handle.proxy_called is False, (
            "Version gate must short-circuit BEFORE any HTTP call via " "handle.proxy()"
        )
        assert len(handler.events) == 1
        assert handler.events[0]["type"] == "agent_error"
        assert handler.events[0]["content"] == EMAIL_QUERY_VERSION_UPGRADE_MESSAGE
        assert relay_calls == []

    def test_proxy_init_raises_sidecar_error(self, monkeypatch):
        fake_handle = _FakeHandle(api_version="2.4", init_error=SidecarError("down"))
        monkeypatch.setattr(
            daemon_client_module, "acquire_handle", lambda agent_id="email": fake_handle
        )
        relay_calls = []
        monkeypatch.setattr(
            relay_module,
            "relay_query",
            lambda *a, **k: relay_calls.append((a, k)),
        )

        handler = _FakeSSEHandler()
        _dispatch_email_query(handler, _make_request(), [], "model")

        assert fake_handle.proxy_called is True
        assert len(handler.events) == 1
        assert handler.events[0]["type"] == "agent_error"
        assert "down" in handler.events[0]["content"]
        assert relay_calls == []

    def test_proxy_init_not_ready_emits_hint_never_calls_relay(self, monkeypatch):
        fake_handle = _FakeHandle(
            api_version="2.4",
            init_result=(503, {"hint": "Lemonade Server not reachable"}),
        )
        monkeypatch.setattr(
            daemon_client_module, "acquire_handle", lambda agent_id="email": fake_handle
        )
        relay_calls = []
        monkeypatch.setattr(
            relay_module,
            "relay_query",
            lambda *a, **k: relay_calls.append((a, k)),
        )

        handler = _FakeSSEHandler()
        _dispatch_email_query(handler, _make_request(), [], "model")

        assert len(handler.events) == 1
        content = handler.events[0]["content"].lower()
        assert "isn't ready" in content
        assert "lemonade server not reachable" in content
        assert relay_calls == []

    def test_cancelled_before_relay_short_circuits_no_relay_call(self, monkeypatch):
        fake_handle = _FakeHandle(api_version="2.4")
        monkeypatch.setattr(
            daemon_client_module, "acquire_handle", lambda agent_id="email": fake_handle
        )
        relay_calls = []
        monkeypatch.setattr(
            relay_module,
            "relay_query",
            lambda *a, **k: relay_calls.append((a, k)),
        )

        handler = _FakeSSEHandler()
        handler.cancelled.set()
        _dispatch_email_query(handler, _make_request(), [], "model")

        assert relay_calls == []
        # No terminal error either — a cooperative cancel is not a failure.
        assert handler.events == []

    def test_happy_path_calls_relay_query_with_expected_kwargs(self, monkeypatch):
        fake_handle = _FakeHandle(api_version="2.4")
        monkeypatch.setattr(
            daemon_client_module, "acquire_handle", lambda agent_id="email": fake_handle
        )

        calls = []

        def _fake_relay_query(handler, proxy, *, query, context, model_id=None, **kw):
            calls.append(
                {
                    "handler": handler,
                    "proxy": proxy,
                    "query": query,
                    "context": context,
                    "model_id": model_id,
                }
            )

        monkeypatch.setattr(relay_module, "relay_query", _fake_relay_query)

        handler = _FakeSSEHandler()
        history_pairs = [("hello", "hi there")]
        request = _make_request(message="what's up")
        _dispatch_email_query(handler, request, history_pairs, "model-x")

        assert len(calls) == 1
        call = calls[0]
        assert call["handler"] is handler
        assert call["proxy"] is fake_handle._proxy
        assert call["query"] == "what's up"
        assert call["context"] == _query_context_from_history(history_pairs)
        assert call["model_id"] == "model-x"
        # No terminal error was emitted before the relay handoff.
        assert handler.events == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
