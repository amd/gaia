# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for POST /api/chat/user-input (#2595).

Calls the router coroutine directly (mirrors the direct-call convention in
test_detached_run_gate.py), patching ``_active_sse_handlers`` rather than
spinning up a full FastAPI app + TestClient.
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from gaia.ui import _chat_helpers as helpers
from gaia.ui.email_sidecar.errors import SidecarHTTPError
from gaia.ui.routers.chat import UserInputRequest, user_input

pytestmark = pytest.mark.asyncio


class _FakeHandler:
    def __init__(self, *, delivered=True, raises=None):
        self._delivered = delivered
        self._raises = raises
        self.calls = []

    def resolve_relay_input(self, request_id, value):
        self.calls.append((request_id, value))
        if self._raises is not None:
            raise self._raises
        return self._delivered


async def test_delivers_answer_to_the_session_handler():
    handler = _FakeHandler(delivered=True)
    with patch.object(helpers, "_active_sse_handlers", {"session-1": handler}):
        result = await user_input(
            UserInputRequest(session_id="session-1", request_id="req-1", value="gmail")
        )

    assert result == {"status": "ok", "request_id": "req-1"}
    assert handler.calls == [("req-1", "gmail")]


async def test_unknown_session_is_404():
    with patch.object(helpers, "_active_sse_handlers", {}):
        with pytest.raises(HTTPException) as exc_info:
            await user_input(
                UserInputRequest(
                    session_id="no-such-session", request_id="req-1", value="gmail"
                )
            )
    assert exc_info.value.status_code == 404


async def test_no_pending_question_is_404_not_a_fake_accept():
    handler = _FakeHandler(delivered=False)
    with patch.object(helpers, "_active_sse_handlers", {"session-1": handler}):
        with pytest.raises(HTTPException) as exc_info:
            await user_input(
                UserInputRequest(
                    session_id="session-1", request_id="req-stale", value="gmail"
                )
            )
    assert exc_info.value.status_code == 404


async def test_sidecar_rejection_surfaces_as_409_not_swallowed():
    handler = _FakeHandler(raises=SidecarHTTPError(409, "question no longer pending"))
    with patch.object(helpers, "_active_sse_handlers", {"session-1": handler}):
        with pytest.raises(HTTPException) as exc_info:
            await user_input(
                UserInputRequest(
                    session_id="session-1", request_id="req-1", value="gmail"
                )
            )
    assert exc_info.value.status_code == 409
    assert "question no longer pending" in exc_info.value.detail
