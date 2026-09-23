# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The per-agent relay profiles, and the flagship's dispatch path (#4161).

Two jobs:

- pin the behaviour that differs per agent (contract floor, copy, session_id,
  the ``/v1/<agent>`` prefix the proxy builds),
- keep the GAIA tool-label and mutating-tool sets honest: a name that no tool
  actually registers is a label that will never render, so the map rots
  silently. Guarded by scanning the real ``@tool`` declarations.
"""

import ast
import threading
from pathlib import Path

import pytest

import gaia.ui.email_sidecar.daemon_client as daemon_client_module
import gaia.ui.email_sidecar.relay as relay_module
from gaia.ui._chat_helpers import _dispatch_sidecar_query, _query_context_from_history
from gaia.ui.email_sidecar.profiles import (
    EMAIL_PROFILE,
    GAIA_PROFILE,
    api_version_supported,
    profile_for,
)
from gaia.ui.email_sidecar.proxy import EmailSidecarProxy, SidecarProxy
from gaia.ui.models import ChatRequest

REPO_ROOT = Path(__file__).resolve().parents[4]


# ── The proxy builds each agent's own path prefix ────────────────────────────


class TestProxyPrefix:
    def test_gaia_proxy_targets_v1_gaia(self):
        assert SidecarProxy("http://x", agent_id="gaia").prefix == "/v1/gaia"

    def test_email_subclass_is_pinned_to_email(self):
        assert EmailSidecarProxy("http://x").prefix == "/v1/email"

    def test_email_subclass_refuses_another_agent(self):
        # Its mailbox routes are hardcoded /v1/email, so aiming it elsewhere
        # could only 404. Refuse rather than silently ignore the argument.
        with pytest.raises(TypeError, match="email-only"):
            EmailSidecarProxy("http://x", agent_id="gaia")

    def test_only_email_carries_mailbox_routes(self):
        generic = SidecarProxy("http://x", agent_id="gaia")
        assert not hasattr(generic, "triage")
        assert not hasattr(generic, "provision")
        assert hasattr(EmailSidecarProxy("http://x"), "triage")

    def test_query_and_cancel_paths_are_agent_scoped(self):
        p = SidecarProxy("http://x", agent_id="gaia")
        assert f"{p.prefix}/query" == "/v1/gaia/query"
        assert f"{p.prefix}/query/abc/cancel" == "/v1/gaia/query/abc/cancel"


# ── Contract floors ──────────────────────────────────────────────────────────


class TestVersionFloors:
    @pytest.mark.parametrize(
        "version,ok", [("2.13", True), ("2.12", True), ("2.11", False), ("3.0", True)]
    )
    def test_gaia_floor_is_2_12(self, version, ok):
        assert api_version_supported(GAIA_PROFILE, version) is ok

    @pytest.mark.parametrize(
        "version,ok", [("2.4", True), ("2.3", False), ("1.9", False)]
    )
    def test_email_floor_is_unchanged(self, version, ok):
        assert api_version_supported(EMAIL_PROFILE, version) is ok

    @pytest.mark.parametrize("version", [None, "", "not-a-version"])
    def test_absent_or_junk_version_is_refused(self, version):
        # No evidence the routes exist is not the same as "probably fine".
        assert api_version_supported(GAIA_PROFILE, version) is False


# ── Tool maps name real tools ────────────────────────────────────────────────


def _registered_tool_names() -> set:
    """Every ``@tool``-decorated function name reachable by the flagship.

    Walked with ``ast`` rather than matched with a regex: a ``@tool(...)``
    decorator's arguments span several lines, and the regex that tried to pair
    one with the next ``def`` both missed real tools (``run_shell_command``)
    and matched names that were never tools at all.
    """
    names = set()
    roots = [
        "src/gaia/agents/tools",
        "src/gaia/sd",
        "src/gaia/vlm",
        "src/gaia/agents/code_index",
        "hub/agents/chat/python",
        "hub/agents/gaia/python",
    ]

    def _is_tool_decorator(node) -> bool:
        target = node.func if isinstance(node, ast.Call) else node
        name = (
            target.attr
            if isinstance(target, ast.Attribute)
            else getattr(target, "id", None)
        )
        return name == "tool"

    for root in roots:
        for path in (REPO_ROOT / root).rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(errors="replace"))
            except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                    _is_tool_decorator(d) for d in node.decorator_list
                ):
                    names.add(node.name)
    return names


#: Tools whose effects the user must be able to see scroll past — arbitrary
#: code execution and the durable writes. Asserted to be MARKED MUTATING, which
#: the listed->real guard below cannot do: it only catches names that do not
#: exist, never a real tool the map forgot. ``run_shell_command`` was missing.
_MUST_BE_MUTATING = frozenset(
    {
        "run_shell_command",
        "run_python",
        "execute_python_file",
        "write_file",
        "edit_file",
        "download_file",
        "install_skill",
    }
)


class TestGaiaToolMapsAreReal:
    """A label keyed on a tool that does not exist never renders; a mutating
    entry that does not exist silently stops warning about a real write."""

    def test_repo_scan_finds_tools_at_all(self):
        # Guards the guard: a broken scan would make both tests below vacuous.
        found = _registered_tool_names()
        # Spot-check one tool per registration style: a plain decorator, a
        # multi-line one inside a register_*_tools closure, and a mixin's.
        for tool in (
            "read_file",
            "search_web",
            "query_documents",
            "generate_image",
            "run_shell_command",
        ):
            assert tool in found, f"scan missed {tool}"
        assert len(found) > 70

    def test_every_label_names_a_registered_tool(self):
        unknown = sorted(set(GAIA_PROFILE.tool_labels) - _registered_tool_names())
        assert not unknown, f"labels for tools that do not exist: {unknown}"

    def test_every_mutating_entry_names_a_registered_tool(self):
        unknown = sorted(GAIA_PROFILE.mutating_tools - _registered_tool_names())
        assert not unknown, f"mutating entries for tools that do not exist: {unknown}"

    def test_the_consequential_tools_are_marked_mutating(self):
        """The reverse direction: a real tool the map forgot is invisible to
        the listed->real check, and silently loses its visible status line."""
        missing = sorted(_MUST_BE_MUTATING - GAIA_PROFILE.mutating_tools)
        assert not missing, f"durable/executing tools not marked mutating: {missing}"

    def test_the_must_be_mutating_list_is_not_stale(self):
        """Guards the guard: a renamed tool would make the check vacuous."""
        unknown = sorted(_MUST_BE_MUTATING - _registered_tool_names())
        assert not unknown, f"_MUST_BE_MUTATING names non-existent tools: {unknown}"

    def test_read_only_tools_are_not_marked_mutating(self):
        for tool in ("read_file", "search_web", "query_data", "list_skills"):
            assert tool not in GAIA_PROFILE.mutating_tools


# ── Dispatch: the flagship reaches the relay, not registry.create_agent ──────


class _FakeSSEHandler:
    def __init__(self):
        self.events = []
        self.cancelled = threading.Event()

    def _emit(self, event):
        self.events.append(event)


class _FakeProxy:
    def __init__(self, init_result=(200, {})):
        self._init_result = init_result

    def init(self):
        return self._init_result


class _FakeHandle:
    def __init__(self, api_version="2.13", init_result=(200, {})):
        self.api_version = api_version
        self._proxy = _FakeProxy(init_result)

    def proxy(self):
        return self._proxy


class TestGaiaDispatch:
    def _patch(self, monkeypatch, handle):
        acquired = []

        def _acquire(agent_id="email"):
            acquired.append(agent_id)
            return handle

        monkeypatch.setattr(daemon_client_module, "acquire_handle", _acquire)
        calls = []
        monkeypatch.setattr(
            relay_module, "relay_query", lambda *a, **k: calls.append((a, k))
        )
        return acquired, calls

    def test_relays_to_the_gaia_sidecar_with_its_profile(self, monkeypatch):
        handle = _FakeHandle()
        acquired, calls = self._patch(monkeypatch, handle)
        handler = _FakeSSEHandler()
        request = ChatRequest(session_id="s1", message="hi", agent_type="gaia")

        _dispatch_sidecar_query(handler, request, [("a", "b")], "m", "gaia", "sess-1")

        assert acquired == ["gaia"], "must ensure the gaia sidecar, not email's"
        assert len(calls) == 1
        _, kwargs = calls[0]
        assert kwargs["profile"] is GAIA_PROFILE
        assert kwargs["session_id"] == "sess-1"
        assert kwargs["context"] == _query_context_from_history([("a", "b")])
        assert handler.events == []

    def test_old_binary_is_refused_with_gaia_copy(self, monkeypatch):
        _, calls = self._patch(monkeypatch, _FakeHandle(api_version="2.11"))
        handler = _FakeSSEHandler()
        request = ChatRequest(session_id="s1", message="hi", agent_type="gaia")

        _dispatch_sidecar_query(handler, request, [], "m", "gaia")

        assert calls == []
        assert handler.events == [
            {"type": "agent_error", "content": GAIA_PROFILE.version_upgrade_message}
        ]
        assert "email" not in handler.events[0]["content"].lower()

    def test_not_ready_warns_but_still_runs_the_turn(self, monkeypatch):
        """The flagship's /init is a probe, not a gate: it reports unreachable
        against an auth-protected Lemonade that answers /query fine, and
        halting on that would kill a working agent."""
        handle = _FakeHandle(init_result=(503, {"hint": "Lemonade is not running"}))
        _, calls = self._patch(monkeypatch, handle)
        handler = _FakeSSEHandler()
        request = ChatRequest(session_id="s1", message="hi", agent_type="gaia")

        _dispatch_sidecar_query(handler, request, [], "m", "gaia")

        assert len(calls) == 1, "a not-ready probe must not cancel the turn"
        assert len(handler.events) == 1
        message = handler.events[0]["message"]
        assert handler.events[0]["type"] == "status"
        # Framed as a probe result, not asserted as fact — it is wrong often
        # enough that this path exists at all.
        assert "Readiness check reported" in message
        assert "Lemonade is not running" in message

    def test_email_not_ready_still_blocks(self, monkeypatch):
        """Email's /init IS a gate — provisioning is a step the user finishes
        on the agent card, and a triage against an unprovisioned mailbox
        burns a long model call to reach the same answer."""
        handle = _FakeHandle(api_version="2.4", init_result=(503, {"hint": "no inbox"}))
        _, calls = self._patch(monkeypatch, handle)
        handler = _FakeSSEHandler()
        request = ChatRequest(session_id="s1", message="hi", agent_type="email")

        _dispatch_sidecar_query(handler, request, [], "m", "email")

        assert calls == []
        assert handler.events[0]["type"] == "agent_error"
        assert "email agent isn't ready" in handler.events[0]["content"]

    def test_unknown_agent_fails_loudly(self, monkeypatch):
        self._patch(monkeypatch, _FakeHandle())
        request = ChatRequest(session_id="s1", message="hi", agent_type="gaia")
        with pytest.raises(ValueError, match="no sidecar relay profile"):
            _dispatch_sidecar_query(
                _FakeSSEHandler(), request, [], "m", "not-a-sidecar"
            )


# ── session_id is gated per agent (both request models forbid extras) ────────


class TestSessionIdGating:
    def _body_for(self, profile, session_id):
        sent = {}

        class _P:
            agent_id = profile.agent_id

            def query_stream(self, body, **kw):
                sent.update(body)
                return iter([{"type": "final", "answer": "ok"}])

        relay_module.relay_query(
            _FakeSSEHandler(),
            _P(),
            query="q",
            context=[],
            profile=profile,
            session_id=session_id,
        )
        return sent

    def test_gaia_sends_session_id(self):
        assert self._body_for(GAIA_PROFILE, "sess-9")["session_id"] == "sess-9"

    def test_email_does_not(self):
        assert "session_id" not in self._body_for(EMAIL_PROFILE, "sess-9")

    def test_gaia_omits_it_when_absent(self):
        assert "session_id" not in self._body_for(GAIA_PROFILE, None)


# ── Per-agent copy never says "email" for the flagship ───────────────────────


class TestCopyIsAgentSpecific:
    def test_stream_ended_message_names_gaia(self):
        msg = relay_module._stream_ended_message(GAIA_PROFILE)
        assert msg.startswith("GAIA agent stream ended")

    def test_email_keeps_its_pinned_string(self):
        assert (
            relay_module._stream_ended_message(EMAIL_PROFILE)
            == relay_module.STREAM_ENDED_UNEXPECTEDLY
        )

    def test_mutating_status_line_names_the_right_kind_of_change(self):
        handler = _FakeSSEHandler()
        relay_module._dispatch_one(
            handler, {"type": "tool_call", "tool": "write_file"}, GAIA_PROFILE
        )
        statuses = [e for e in handler.events if e["type"] == "status"]
        assert statuses and "local change: write_file" in statuses[0]["message"]

    def test_gaia_tool_labels_are_used(self):
        handler = _FakeSSEHandler()
        relay_module._dispatch_one(
            handler, {"type": "tool_call", "tool": "search_web"}, GAIA_PROFILE
        )
        assert handler.events[0]["detail"] == "Searching the web"

    def test_unlisted_tool_humanizes_rather_than_showing_a_raw_name(self):
        handler = _FakeSSEHandler()
        relay_module._dispatch_one(
            handler, {"type": "tool_call", "tool": "some_new_tool"}, GAIA_PROFILE
        )
        assert handler.events[0]["detail"] == "some new tool"


def test_profile_for_unknown_agent_is_none():
    assert profile_for("nope") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
