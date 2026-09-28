# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for POST /api/chat/user-input (#2595).

Calls the router coroutine directly (mirrors the direct-call convention in
test_detached_run_gate.py), patching ``_active_sse_handlers`` rather than
spinning up a full FastAPI app + TestClient.

Two run shapes can be waiting on an answer: an in-process agent blocked in
``resolve_user_input`` (general path), or an email-relay run blocked on the
sidecar (``resolve_relay_input``). The endpoint tries the general path first
and falls back to the relay path only when nothing was pending there.
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from gaia.ui import _chat_helpers as helpers
from gaia.ui.email_sidecar.errors import SidecarHTTPError
from gaia.ui.routers.chat import UserInputRequest, user_input

pytestmark = pytest.mark.asyncio


class _FakeHandler:
    def __init__(
        self, *, user_input_delivered=False, relay_delivered=True, relay_raises=None
    ):
        self._user_input_delivered = user_input_delivered
        self._relay_delivered = relay_delivered
        self._relay_raises = relay_raises
        self.user_input_calls = []
        self.relay_calls = []

    def resolve_user_input(self, request_id, value):
        self.user_input_calls.append((request_id, value))
        return self._user_input_delivered

    def resolve_relay_input(self, request_id, value):
        self.relay_calls.append((request_id, value))
        if self._relay_raises is not None:
            raise self._relay_raises
        return self._relay_delivered


async def test_delivers_via_the_general_in_process_path_first():
    handler = _FakeHandler(user_input_delivered=True)
    with patch.object(helpers, "_active_sse_handlers", {"session-1": handler}):
        result = await user_input(
            UserInputRequest(session_id="session-1", request_id="req-1", value="gmail")
        )

    assert result == {"status": "ok", "request_id": "req-1"}
    assert handler.user_input_calls == [("req-1", "gmail")]
    # The relay path must not even be tried once the general path delivered.
    assert handler.relay_calls == []


async def test_falls_back_to_the_relay_path_when_nothing_pending_in_process():
    handler = _FakeHandler(user_input_delivered=False, relay_delivered=True)
    with patch.object(helpers, "_active_sse_handlers", {"session-1": handler}):
        result = await user_input(
            UserInputRequest(session_id="session-1", request_id="req-1", value="gmail")
        )

    assert result == {"status": "ok", "request_id": "req-1"}
    assert handler.user_input_calls == [("req-1", "gmail")]
    assert handler.relay_calls == [("req-1", "gmail")]


async def test_unknown_session_is_404():
    with patch.object(helpers, "_active_sse_handlers", {}):
        with pytest.raises(HTTPException) as exc_info:
            await user_input(
                UserInputRequest(
                    session_id="no-such-session", request_id="req-1", value="gmail"
                )
            )
    assert exc_info.value.status_code == 404


async def test_no_pending_question_anywhere_is_404_not_a_fake_accept():
    handler = _FakeHandler(user_input_delivered=False, relay_delivered=False)
    with patch.object(helpers, "_active_sse_handlers", {"session-1": handler}):
        with pytest.raises(HTTPException) as exc_info:
            await user_input(
                UserInputRequest(
                    session_id="session-1", request_id="req-stale", value="gmail"
                )
            )
    assert exc_info.value.status_code == 404


async def test_sidecar_rejection_surfaces_as_409_not_swallowed():
    handler = _FakeHandler(
        user_input_delivered=False,
        relay_raises=SidecarHTTPError(409, "question no longer pending"),
    )
    with patch.object(helpers, "_active_sse_handlers", {"session-1": handler}):
        with pytest.raises(HTTPException) as exc_info:
            await user_input(
                UserInputRequest(
                    session_id="session-1", request_id="req-1", value="gmail"
                )
            )
    assert exc_info.value.status_code == 409
    assert "question no longer pending" in exc_info.value.detail
