# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for SSEOutputHandler tool confirmation flow.

Tests the blocking confirm_tool_execution / resolve_tool_confirmation handshake
used by the tool execution guardrails feature (PR #565, re-implemented in PR #604).
"""

import threading
import time

import pytest

from gaia.agents.base.console import OutputHandler
from gaia.ui.sse_handler import SSEOutputHandler

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def handler():
    """Create a fresh SSEOutputHandler for each test."""
    return SSEOutputHandler()


def _drain(handler: SSEOutputHandler):
    """Drain all events from the handler's queue and return as a list."""
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


def _wait_for_pending_confirmation(handler: SSEOutputHandler):
    """Wait until confirm_tool_execution has installed its pending event."""
    deadline = time.time() + 2.0
    while handler._confirm_event is None and time.time() < deadline:
        time.sleep(0.05)
    assert handler._confirm_event is not None


# ===========================================================================
# confirm_tool_execution — cancellation
# ===========================================================================


class TestConfirmToolExecutionTimeout:
    """confirm_tool_execution returns False after the safety-net timeout."""

    def test_timeout_returns_false(self, handler):
        """When no resolve arrives, confirm_tool_execution returns False after timeout."""
        result = handler.confirm_tool_execution(
            "run_shell_command", {"cmd": "ls"}, timeout=0.3
        )
        assert result is False


class TestConfirmToolExecutionCancellation:
    """confirm_tool_execution returns False when the stream is cancelled."""

    def test_cancellation_returns_false(self, handler):
        """When cancelled is set, confirm_tool_execution returns False."""
        result_holder = {"result": None}

        def run_confirm():
            result_holder["result"] = handler.confirm_tool_execution(
                "run_shell_command", {"cmd": "ls"}
            )

        t = threading.Thread(target=run_confirm)
        t.start()

        # Wait for the confirmation to be set up
        time.sleep(0.2)

        # Simulate cancellation
        handler.cancelled.set()

        t.join(timeout=3.0)
        assert not t.is_alive()
        assert result_holder["result"] is False

    def test_emits_permission_request_event(self, handler):
        """confirm_tool_execution emits a permission_request event."""
        result_holder = {}

        def run_confirm():
            result_holder["result"] = handler.confirm_tool_execution(
                "run_shell_command", {"cmd": "ls"}
            )

        t = threading.Thread(target=run_confirm)
        t.start()

        # Wait for the event to be emitted
        time.sleep(0.2)

        events = _drain(handler)
        permission_events = [
            e for e in events if e and e.get("type") == "permission_request"
        ]
        assert len(permission_events) == 1
        assert permission_events[0]["tool"] == "run_shell_command"
        assert permission_events[0]["args"] == {"cmd": "ls"}

        # Clean up
        handler.cancelled.set()
        t.join(timeout=3.0)


# ===========================================================================
# confirm_tool_execution — resolve with approve
# ===========================================================================


class TestConfirmToolExecutionApprove:
    """confirm_tool_execution returns True when resolved with approved=True."""

    def test_approve_returns_true(self, handler):
        """Resolving with approved=True unblocks and returns True."""
        result_holder = {"result": None}

        def run_confirm():
            result_holder["result"] = handler.confirm_tool_execution(
                "run_shell_command", {"cmd": "echo hello"}
            )

        t = threading.Thread(target=run_confirm)
        t.start()

        # Wait for the worker to have set up _confirm_event before we resolve.
        # Polling _confirm_result was wrong because it starts at False; the
        # shared helper waits for the event registration point instead.
        _wait_for_pending_confirmation(handler)

        handler.resolve_tool_confirmation(approved=True)

        t.join(timeout=3.0)
        assert not t.is_alive()
        assert result_holder["result"] is True

    def test_approve_sets_confirm_result(self, handler):
        """After approval, _confirm_result is True."""
        result_holder = {}

        def run_confirm():
            result_holder["result"] = handler.confirm_tool_execution("tool", {})

        t = threading.Thread(target=run_confirm)
        t.start()

        _wait_for_pending_confirmation(handler)

        handler.resolve_tool_confirmation(approved=True)
        t.join(timeout=3.0)

        assert handler._confirm_result is True


# ===========================================================================
# confirm_tool_execution — resolve with deny
# ===========================================================================


class TestConfirmToolExecutionDeny:
    """confirm_tool_execution returns False when resolved with approved=False."""

    def test_deny_returns_false(self, handler):
        """Resolving with approved=False unblocks and returns False."""
        result_holder = {"result": None}

        def run_confirm():
            result_holder["result"] = handler.confirm_tool_execution(
                "write_file", {"path": "/etc/passwd"}
            )

        t = threading.Thread(target=run_confirm)
        t.start()

        # See note in test_approve_returns_true: wait for _confirm_event, not
        # _confirm_result. The latter is False from the start.
        _wait_for_pending_confirmation(handler)

        handler.resolve_tool_confirmation(approved=False)

        t.join(timeout=3.0)
        assert not t.is_alive()
        assert result_holder["result"] is False


# ===========================================================================
# resolve_tool_confirmation — no pending confirmation
# ===========================================================================


class TestResolveToolConfirmationNoPending:
    """resolve_tool_confirmation with no pending request just sets the event."""

    def test_no_pending_sets_event(self, handler):
        """Calling resolve with no pending confirm just sets the event/result."""
        handler.resolve_tool_confirmation(approved=True)
        assert handler._confirm_result is True
        assert handler._confirm_event.is_set()


# ===========================================================================
# POST /api/chat/confirm-tool endpoint
# ===========================================================================


class TestConfirmToolEndpoint:
    """Tests for the POST /api/chat/confirm-tool endpoint."""

    @pytest.fixture
    def app(self):
        """Create a minimal FastAPI app with the chat router."""
        from fastapi import FastAPI

        from gaia.ui.routers.chat import router

        app = FastAPI()
        app.include_router(router)
        # Initialize state that the chat router expects (session_locks, semaphore).
        # Note: the confirm-tool endpoint uses _chat_helpers._active_sse_handlers
        # (module-level dict), not app.state.
        app.state.session_locks = {}
        app.state.chat_semaphore = None
        return app

    @pytest.fixture
    def client(self, app):
        """Create a test client."""
        from fastapi.testclient import TestClient

        return TestClient(app)

    def test_confirm_approve_routes_to_handler(self, client, app):
        """Approve action resolves the pending confirmation."""
        from gaia.ui._chat_helpers import _active_sse_handlers

        handler = SSEOutputHandler()
        session_id = "test-session-1"
        _active_sse_handlers[session_id] = handler

        # Set up a pending confirmation
        handler._confirm_event = threading.Event()
        handler._confirm_result = None

        try:
            resp = client.post(
                "/api/chat/confirm-tool",
                json={"session_id": session_id, "approved": True},
            )

            assert resp.status_code == 200
            assert resp.json() == {"status": "ok", "approved": True}
            assert handler._confirm_result is True
            assert handler._confirm_event.is_set()
        finally:
            _active_sse_handlers.pop(session_id, None)

    def test_confirm_deny_routes_to_handler(self, client, app):
        """Deny action resolves the pending confirmation with False."""
        from gaia.ui._chat_helpers import _active_sse_handlers

        handler = SSEOutputHandler()
        session_id = "test-session-2"
        _active_sse_handlers[session_id] = handler

        handler._confirm_event = threading.Event()
        handler._confirm_result = None

        try:
            resp = client.post(
                "/api/chat/confirm-tool",
                json={"session_id": session_id, "approved": False},
            )

            assert resp.status_code == 200
            assert handler._confirm_result is False
        finally:
            _active_sse_handlers.pop(session_id, None)

    def test_confirm_no_active_session_returns_404(self, client):
        """Missing session returns 404."""
        resp = client.post(
            "/api/chat/confirm-tool",
            json={"session_id": "nonexistent", "approved": True},
        )
        assert resp.status_code == 404


# ===========================================================================
# Regression: the SSE path must NOT inherit the deny-by-default (#2210)
# ===========================================================================


class TestSSEPathUnaffectedByDenyByDefault:
    """``OutputHandler.confirm_tool_execution`` now denies when it cannot reach a
    human (#2210). The Agent UI path has a human — the frontend permission modal
    — so it must still emit ``permission_request``, block, and honour whatever
    ``resolve_tool_confirmation`` says.
    """

    def test_permission_request_still_fires_and_approval_still_runs_the_tool(self):
        """End-to-end through the agent gate: request event, then execution."""
        from unittest.mock import patch

        from gaia.agents.base.agent import Agent
        from gaia.agents.base.tools import tool

        class _GatedAgent(Agent):
            CONFIRMATION_REQUIRED_TOOLS = frozenset({"send_now"})

            def __init__(self, console, **kwargs):
                self.sent = []
                self._console_override = console
                super().__init__(**kwargs)

            def _get_system_prompt(self):
                return "gated"

            def _create_console(self):
                return self._console_override

            def _register_tools(self):
                sent = self.sent

                @tool
                def send_now(to: str) -> str:
                    """Send an email immediately. Gated."""
                    sent.append(to)
                    return "SENT"

        handler = SSEOutputHandler()
        with patch("gaia.agents.base.agent.AgentSDK"):
            agent = _GatedAgent(console=handler, silent_mode=True, skip_lemonade=True)

        result_holder = {}

        def run_tool():
            result_holder["result"] = agent._execute_tool("send_now", {"to": "a@b.c"})

        t = threading.Thread(target=run_tool)
        t.start()
        _wait_for_pending_confirmation(handler)

        events = _drain(handler)
        permission = [e for e in events if e.get("type") == "permission_request"]
        assert len(permission) == 1
        assert permission[0]["tool"] == "send_now"
        assert permission[0]["args"] == {"to": "a@b.c"}
        assert permission[0]["confirm_id"]

        handler.resolve_tool_confirmation(approved=True)
        t.join(timeout=3.0)
        assert not t.is_alive()
        assert result_holder["result"] == "SENT"
        assert agent.sent == ["a@b.c"]

    def test_blocking_handler_does_not_auto_deny(self, handler):
        """The handler advertises a live confirmation channel and overrides the
        base default — no synchronous deny before the modal is shown."""
        assert handler.blocking_confirmation is True
        assert handler.auto_approve_gated_tools is False
        assert (
            SSEOutputHandler.confirm_tool_execution
            is not OutputHandler.confirm_tool_execution
        )

    def test_background_mode_still_denies_with_the_unattended_reason(self):
        """Autonomous runs keep their existing auto-deny event and now surface
        the same wording in the tool result the model sees."""
        handler = SSEOutputHandler(background_mode=True)
        assert handler.confirm_tool_execution("send_now", {}) is False

        events = _drain(handler)
        denied = [e for e in events if e.get("type") == "tool_confirm_denied"]
        assert len(denied) == 1
        assert denied[0]["reason"] == "unattended"
        assert "cannot run" in denied[0]["message"]
        assert handler.confirmation_denied_reason("send_now") == denied[0]["message"]
