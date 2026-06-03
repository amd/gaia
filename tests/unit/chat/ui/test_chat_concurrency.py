# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for GAIA Agent UI chat endpoint concurrency control.

Tests the per-session lock (409) and global semaphore (429) mechanisms
in the /api/chat/send endpoint, as well as lock release on error paths.
"""

import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app


@pytest.fixture
def app():
    """Create FastAPI app with in-memory database."""
    return create_app(db_path=":memory:")


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def db(app):
    return app.state.db


@pytest.fixture
def session_id(client):
    """Create a session and return its ID."""
    resp = client.post("/api/sessions", json={})
    return resp.json()["id"]


class TestSessionLockConflict:
    """Tests for same-session conflict handling."""

    def test_concurrent_request_returns_409_if_session_lock_is_held(
        self, app, session_id
    ):
        """When a session lock is held, a second request should get 409."""
        # Pre-acquire the session lock to simulate a stuck request
        lock = asyncio.Lock()

        async def _hold_lock():
            await lock.acquire()

        loop = asyncio.new_event_loop()
        loop.run_until_complete(_hold_lock())

        # Inject the held lock into session_locks
        app.state.session_locks[session_id] = lock

        client = TestClient(app)
        with patch("gaia.ui.server._get_chat_response") as mock:
            mock.return_value = "force-released response"
            resp = client.post(
                "/api/chat/send",
                json={
                    "session_id": session_id,
                    "message": "should conflict while another turn is active",
                    "stream": False,
                },
            )
        assert resp.status_code == 409
        assert "already in progress" in resp.json()["detail"]

        # Cleanup
        lock.release()
        loop.close()

    def test_different_sessions_not_blocked(self, client, db):
        """Requests to different sessions don't interfere."""
        s1 = client.post("/api/sessions", json={}).json()["id"]
        s2 = client.post("/api/sessions", json={}).json()["id"]

        with patch("gaia.ui.server._get_chat_response") as mock:
            mock.return_value = "ok"

            r1 = client.post(
                "/api/chat/send",
                json={"session_id": s1, "message": "hi", "stream": False},
            )
            r2 = client.post(
                "/api/chat/send",
                json={"session_id": s2, "message": "hi", "stream": False},
            )
            assert r1.status_code == 200
            assert r2.status_code == 200


class TestGlobalSemaphore429:
    """Tests for global concurrency semaphore returning 429 Too Many Requests."""

    def test_semaphore_exhausted_returns_429(self):
        """When the global semaphore is exhausted, a request gets 429.

        Implementation notes:

        - We use ``Semaphore(0)`` rather than a Semaphore(1) we manually
          exhaust on a side event loop. The old approach tied the
          semaphore's internal waiter list to a loop different from the
          one TestClient uses, producing flaky cross-loop behaviour
          (sometimes 429 in 60 s, sometimes hung). A semaphore with
          starting value 0 is exhausted from birth and works on any loop.

        - The endpoint waits up to ``timeout=60.0`` for the semaphore
          (see chat.py — long enough to cushion sequential workloads
          like the eval runner). We patch ``asyncio.wait_for`` to a
          tiny timeout so the test runs in <1 s instead of >60 s.
        """
        from unittest.mock import patch as _patch

        app = create_app(db_path=":memory:")
        db = app.state.db
        sid = db.create_session(title="Test")["id"]

        # Permanently-exhausted semaphore — no waiters needed.
        app.state.chat_semaphore = asyncio.Semaphore(0)

        # Patch the in-endpoint wait_for so the test isn't dominated by
        # the production 60 s gate. We wrap the real wait_for and force
        # a short timeout; everything else (acquire, TimeoutError raise)
        # runs unchanged.
        real_wait_for = asyncio.wait_for

        async def _fast_wait_for(awaitable, timeout):  # noqa: ARG001
            return await real_wait_for(awaitable, timeout=0.2)

        with _patch("gaia.ui.routers.chat.asyncio.wait_for", _fast_wait_for):
            client = TestClient(app)
            resp = client.post(
                "/api/chat/send",
                json={"session_id": sid, "message": "blocked", "stream": False},
            )

        assert resp.status_code == 429
        assert "busy" in resp.json()["detail"]

    def test_semaphore_released_after_non_streaming_request(self, client, session_id):
        """After a non-streaming request completes, the semaphore is released."""
        with patch("gaia.ui.server._get_chat_response") as mock:
            mock.return_value = "response"

            resp = client.post(
                "/api/chat/send",
                json={
                    "session_id": session_id,
                    "message": "hi",
                    "stream": False,
                },
            )
            assert resp.status_code == 200

        # A subsequent request should succeed (semaphore was released)
        with patch("gaia.ui.server._get_chat_response") as mock:
            mock.return_value = "response 2"
            resp = client.post(
                "/api/chat/send",
                json={
                    "session_id": session_id,
                    "message": "hi again",
                    "stream": False,
                },
            )
            assert resp.status_code == 200


class TestLockReleaseOnError:
    """Tests that locks are properly released on error paths."""

    def test_session_lock_released_after_chat_error(self, client, session_id, app):
        """If _get_chat_response raises, the session lock should still be released."""
        with patch("gaia.ui.server._get_chat_response") as mock:
            mock.side_effect = Exception("LLM crashed")

            resp = client.post(
                "/api/chat/send",
                json={
                    "session_id": session_id,
                    "message": "boom",
                    "stream": False,
                },
            )
            assert resp.status_code == 500

        # Session lock should be released — a new request should succeed
        with patch("gaia.ui.server._get_chat_response") as mock:
            mock.return_value = "recovered"
            resp = client.post(
                "/api/chat/send",
                json={
                    "session_id": session_id,
                    "message": "retry",
                    "stream": False,
                },
            )
            assert resp.status_code == 200

    def test_semaphore_released_after_chat_error(self, client, session_id, app):
        """If _get_chat_response raises, the global semaphore should be released."""
        with patch("gaia.ui.server._get_chat_response") as mock:
            mock.side_effect = Exception("Backend down")

            client.post(
                "/api/chat/send",
                json={
                    "session_id": session_id,
                    "message": "fail",
                    "stream": False,
                },
            )

        # Semaphore should be released — another session's request should work
        s2 = client.post("/api/sessions", json={}).json()["id"]
        with patch("gaia.ui.server._get_chat_response") as mock:
            mock.return_value = "ok"
            resp = client.post(
                "/api/chat/send",
                json={"session_id": s2, "message": "fine", "stream": False},
            )
            assert resp.status_code == 200

    def test_session_not_found_doesnt_acquire_locks(self, client, app):
        """404 for missing session should not acquire or leak any lock."""
        resp = client.post(
            "/api/chat/send",
            json={
                "session_id": "nonexistent",
                "message": "hi",
                "stream": False,
            },
        )
        assert resp.status_code == 404

        # Verify no lock was created for the nonexistent session
        assert "nonexistent" not in app.state.session_locks


class TestStreamingLockRelease:
    """Tests that locks are released properly for streaming requests."""

    def test_streaming_releases_locks_after_consuming(self, client, session_id):
        """Streaming generator should release both locks once fully consumed."""
        with patch("gaia.ui.server._stream_chat_response") as mock:

            async def fake_stream(*args, **kwargs):
                yield 'data: {"type": "chunk", "content": "Hi"}\n\n'
                yield 'data: {"type": "done", "content": "Hi"}\n\n'

            mock.return_value = fake_stream()

            resp = client.post(
                "/api/chat/send",
                json={
                    "session_id": session_id,
                    "message": "stream me",
                    "stream": True,
                },
            )
            assert resp.status_code == 200
            # Consume the response (TestClient reads it fully)
            _ = resp.text

        # After streaming completes, locks should be released.
        # A subsequent non-streaming request should succeed.
        with patch("gaia.ui.server._get_chat_response") as mock:
            mock.return_value = "after stream"
            resp = client.post(
                "/api/chat/send",
                json={
                    "session_id": session_id,
                    "message": "next",
                    "stream": False,
                },
            )
            assert resp.status_code == 200

    def test_streaming_response_has_correct_headers(self, client, session_id):
        """Streaming response should have SSE-appropriate headers."""
        with patch("gaia.ui.server._stream_chat_response") as mock:

            async def fake_stream(*args, **kwargs):
                yield 'data: {"type": "done", "content": "x"}\n\n'

            mock.return_value = fake_stream()

            resp = client.post(
                "/api/chat/send",
                json={
                    "session_id": session_id,
                    "message": "test",
                    "stream": True,
                },
            )
            assert "text/event-stream" in resp.headers.get("content-type", "")
            assert resp.headers.get("cache-control") == "no-cache"
