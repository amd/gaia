# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for SSEOutputHandler tool confirmation flow.

Tests the blocking confirm_tool_execution / resolve_tool_confirmation handshake
used by the tool execution guardrails feature (PR #565, re-implemented in PR #604).
"""

import threading
import time

import pytest

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

        # Wait for the confirmation to be set up
        deadline = time.time() + 2.0
        while handler._confirm_result is None and time.time() < deadline:
            time.sleep(0.05)

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

        deadline = time.time() + 2.0
        while handler._confirm_result is None and time.time() < deadline:
            time.sleep(0.05)

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

        deadline = time.time() + 2.0
        while handler._confirm_result is None and time.time() < deadline:
            time.sleep(0.05)

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
