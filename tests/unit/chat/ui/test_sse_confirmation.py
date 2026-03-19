# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for SSEOutputHandler tool confirmation flow.

Tests the blocking confirm_tool_execution / resolve_confirmation handshake
used by the tool execution guardrails feature.

NOTE: The tool confirmation flow was removed in PR #566 (round 5 fixes) as
part of simplifying the Agent UI. These tests are skipped until the feature
is re-implemented.
"""

import threading
import time

import pytest

pytestmark = pytest.mark.skip(
    reason="Tool confirmation flow removed in PR #566; tests skipped until re-implemented"
)

from gaia.ui.sse_handler import SSEOutputHandler  # noqa: E402

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
# confirm_tool_execution — timeout
# ===========================================================================


class TestConfirmToolExecutionTimeout:
    """confirm_tool_execution returns False after timeout."""

    def test_timeout_returns_false(self, handler, monkeypatch):
        """When no resolve arrives, confirm_tool_execution returns False."""
        # Patch the module-level constant to a tiny value so the test runs fast
        monkeypatch.setattr("gaia.ui.sse_handler.TOOL_CONFIRM_TIMEOUT_SECONDS", 0.3)

        result = handler.confirm_tool_execution("run_shell_command", {"cmd": "ls"})

        assert result is False
        # A tool_confirm event should have been emitted
        events = _drain(handler)
        confirm_events = [e for e in events if e and e.get("type") == "tool_confirm"]
        assert len(confirm_events) == 1
        assert confirm_events[0]["tool"] == "run_shell_command"
        # A timeout warning should also have been emitted
        warning_events = [
            e
            for e in events
            if e and e.get("type") == "status" and e.get("status") == "warning"
        ]
        assert len(warning_events) == 1
        assert "timed out" in warning_events[0]["message"]

    def test_timeout_clears_internal_state(self, handler, monkeypatch):
        """After timeout, _confirm_id and _confirm_event are cleared."""
        monkeypatch.setattr("gaia.ui.sse_handler.TOOL_CONFIRM_TIMEOUT_SECONDS", 0.3)

        handler.confirm_tool_execution("test_tool", {})

        assert handler._confirm_id is None
        assert handler._confirm_event is None


# ===========================================================================
# confirm_tool_execution — resolve with allow
# ===========================================================================


class TestConfirmToolExecutionAllow:
    """confirm_tool_execution returns True when resolved with allow."""

    def test_allow_returns_true(self, handler):
        """Resolving with allowed=True unblocks and returns True."""
        result_holder = {"result": None}

        def run_confirm():
            result_holder["result"] = handler.confirm_tool_execution(
                "run_shell_command", {"cmd": "echo hello"}
            )

        t = threading.Thread(target=run_confirm)
        t.start()

        # Wait briefly for the confirm to be set up
        deadline = time.time() + 2.0
        while handler._confirm_id is None and time.time() < deadline:
            time.sleep(0.05)

        assert handler._confirm_id is not None
        confirm_id = handler._confirm_id

        success = handler.resolve_confirmation(confirm_id, allowed=True)
        assert success is True

        t.join(timeout=3.0)
        assert not t.is_alive()
        assert result_holder["result"] is True

    def test_allow_clears_internal_state(self, handler):
        """After allow, _confirm_id and _confirm_event are cleared."""
        result_holder = {}

        def run_confirm():
            result_holder["result"] = handler.confirm_tool_execution("tool", {})

        t = threading.Thread(target=run_confirm)
        t.start()

        deadline = time.time() + 2.0
        while handler._confirm_id is None and time.time() < deadline:
            time.sleep(0.05)

        handler.resolve_confirmation(handler._confirm_id, allowed=True)
        t.join(timeout=3.0)

        assert handler._confirm_id is None
        assert handler._confirm_event is None


# ===========================================================================
# confirm_tool_execution — resolve with deny
# ===========================================================================


class TestConfirmToolExecutionDeny:
    """confirm_tool_execution returns False when resolved with deny."""

    def test_deny_returns_false(self, handler):
        """Resolving with allowed=False unblocks and returns False."""
        result_holder = {"result": None}

        def run_confirm():
            result_holder["result"] = handler.confirm_tool_execution(
                "write_file", {"path": "/etc/passwd"}
            )

        t = threading.Thread(target=run_confirm)
        t.start()

        deadline = time.time() + 2.0
        while handler._confirm_id is None and time.time() < deadline:
            time.sleep(0.05)

        assert handler._confirm_id is not None
        confirm_id = handler._confirm_id

        success = handler.resolve_confirmation(confirm_id, allowed=False)
        assert success is True  # resolve itself succeeds

        t.join(timeout=3.0)
        assert not t.is_alive()
        assert result_holder["result"] is False


# ===========================================================================
# resolve_confirmation — wrong confirm_id
# ===========================================================================


class TestResolveConfirmationWrongId:
    """resolve_confirmation with wrong confirm_id returns False."""

    def test_wrong_id_returns_false(self, handler):
        """Mismatched confirm_id should not unblock the waiting thread."""
        result_holder = {"result": None}

        def run_confirm():
            result_holder["result"] = handler.confirm_tool_execution("tool", {})

        t = threading.Thread(target=run_confirm)
        t.start()

        deadline = time.time() + 2.0
        while handler._confirm_id is None and time.time() < deadline:
            time.sleep(0.05)

        assert handler._confirm_id is not None

        # Try resolving with a wrong ID
        success = handler.resolve_confirmation("wrong-id-12345", allowed=True)
        assert success is False

        # The thread should still be waiting (not unblocked)
        time.sleep(0.2)
        assert t.is_alive()

        # Now resolve with the correct ID so the thread can exit
        handler.resolve_confirmation(handler._confirm_id, allowed=False)
        t.join(timeout=3.0)
        assert result_holder["result"] is False

    def test_no_pending_confirmation_returns_false(self, handler):
        """resolve_confirmation with no pending request returns False."""
        success = handler.resolve_confirmation("some-id", allowed=True)
        assert success is False


# ===========================================================================
# POST /api/chat/confirm endpoint
# ===========================================================================


class TestConfirmEndpoint:
    """Basic tests for the POST /api/chat/confirm endpoint."""

    @pytest.fixture
    def app(self):
        """Create a minimal FastAPI app with the chat router."""
        from fastapi import FastAPI

        from gaia.ui.routers.chat import router

        app = FastAPI()
        app.include_router(router)
        # Initialize state that the endpoint expects
        app.state.active_sse_handlers = {}
        app.state.session_locks = {}
        app.state.chat_semaphore = None
        return app

    @pytest.fixture
    def client(self, app):
        """Create a test client."""
        from fastapi.testclient import TestClient

        return TestClient(app)

    def test_confirm_allow_routes_to_handler(self, client, app):
        """Allow action resolves the pending confirmation."""
        handler = SSEOutputHandler()
        session_id = "test-session-1"
        app.state.active_sse_handlers[session_id] = handler

        # Set up a pending confirmation
        handler._confirm_event = threading.Event()
        handler._confirm_result = False
        handler._confirm_id = "test-confirm-id"

        resp = client.post(
            "/api/chat/confirm",
            json={
                "session_id": session_id,
                "confirm_id": "test-confirm-id",
                "action": "allow",
                "remember": False,
            },
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        assert handler._confirm_result is True

    def test_confirm_deny_routes_to_handler(self, client, app):
        """Deny action resolves the pending confirmation with False."""
        handler = SSEOutputHandler()
        session_id = "test-session-2"
        app.state.active_sse_handlers[session_id] = handler

        handler._confirm_event = threading.Event()
        handler._confirm_result = True  # Start True to verify it gets set to False
        handler._confirm_id = "test-confirm-id-2"

        resp = client.post(
            "/api/chat/confirm",
            json={
                "session_id": session_id,
                "confirm_id": "test-confirm-id-2",
                "action": "deny",
                "remember": False,
            },
        )

        assert resp.status_code == 200
        assert handler._confirm_result is False

    def test_confirm_no_active_session_returns_404(self, client):
        """Missing session returns 404."""
        resp = client.post(
            "/api/chat/confirm",
            json={
                "session_id": "nonexistent",
                "confirm_id": "some-id",
                "action": "allow",
                "remember": False,
            },
        )
        assert resp.status_code == 404

    def test_confirm_wrong_id_returns_410(self, client, app):
        """Wrong confirm_id returns 410 (expired/mismatch)."""
        handler = SSEOutputHandler()
        session_id = "test-session-3"
        app.state.active_sse_handlers[session_id] = handler

        handler._confirm_event = threading.Event()
        handler._confirm_id = "correct-id"

        resp = client.post(
            "/api/chat/confirm",
            json={
                "session_id": session_id,
                "confirm_id": "wrong-id",
                "action": "allow",
                "remember": False,
            },
        )
        assert resp.status_code == 410

    def test_confirm_invalid_action_returns_422(self, client, app):
        """Invalid action value is rejected by Pydantic validation."""
        handler = SSEOutputHandler()
        session_id = "test-session-4"
        app.state.active_sse_handlers[session_id] = handler

        handler._confirm_event = threading.Event()
        handler._confirm_id = "some-id"

        resp = client.post(
            "/api/chat/confirm",
            json={
                "session_id": session_id,
                "confirm_id": "some-id",
                "action": "maybe",
                "remember": False,
            },
        )
        # Pydantic Literal validation should reject "maybe"
        assert resp.status_code == 422
