# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the persistent connector event-loop bridge (``_loop.py``).

Three properties are verified here:

1. **No cross-loop RuntimeError on repeated refresh (AC2)** — calling a sync
   token wrapper *twice*, with the cache expired between calls, must not raise
   ``RuntimeError: <Lock> is bound to a different event loop``.  This was the
   platform-independent half of the #1579 hang.

2. **Bounded wait (AC3)** — if the async op stalls, the sync wrapper raises a
   ``ConnectorsError`` within ``timeout`` seconds rather than blocking forever.

3. **Contextvar preserved (AC4)** — the agent-id contextvar set in the caller
   thread is visible inside the async coroutine submitted to the loop thread.
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from unittest.mock import patch

import httpx
import pytest
import respx

from gaia.connectors import get_access_token_sync, grant_agent
from gaia.connectors.context import _agent_context, current_agent_id
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.providers import _registry
from gaia.connectors.store import save_connection
from gaia.connectors.tokens import _cache

# -------------------------------------------------------------------------
# Shared fixtures
# -------------------------------------------------------------------------


@pytest.fixture
def google_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
    _registry.clear()
    from gaia.connectors.providers import get as get_provider

    return get_provider("google")


@pytest.fixture
def seeded(google_provider):
    save_connection(
        provider="google",
        account_email="alice@example.com",
        refresh_token="seed-rt",
        scopes=["gmail.readonly"],
        client_id_hash=google_provider.client_id_hash,
    )
    return google_provider


def _ok_token():
    return httpx.Response(
        200, json={"access_token": "BEARER", "expires_in": 3600, "scope": "x"}
    )


# -------------------------------------------------------------------------
# AC2: no cross-loop RuntimeError on repeated refresh
# -------------------------------------------------------------------------


class TestNoLockBoundToOtherLoop:
    """AC2: calling the sync wrapper twice, forcing a refresh both times,
    must NOT raise ``RuntimeError: <Lock> is bound to a different event loop``.

    The root cause of #1579 (platform-independent half): asyncio.Lock created
    in one asyncio.run() loop cannot be used from a subsequent asyncio.run()
    loop (Python ≤ 3.11). On Python 3.12+ the lock binding was fixed, but
    the Windows-only ProactorEventLoop create/teardown churn (the second half)
    is still prevented by the persistent-loop architecture.

    This test verifies the solution: the persistent loop thread means all
    Lock operations happen on the SAME loop, so the issue cannot arise.
    """

    @respx.mock
    def test_repeated_refresh_does_not_raise_cross_loop_error(self, seeded):
        """Force two refreshes from worker threads — each expires the cache
        between calls.  Must succeed on both, no cross-loop RuntimeError."""
        respx.post("https://oauth2.googleapis.com/token").mock(return_value=_ok_token())
        grant_agent("google", "builtin:chat", ["gmail.readonly"])

        errors: list[Exception] = []
        tokens: list[str] = []

        def worker():
            with _agent_context("builtin:chat"):
                try:
                    tok = get_access_token_sync(
                        provider="google", scopes=["gmail.readonly"]
                    )
                    tokens.append(tok)
                except Exception as exc:
                    errors.append(exc)

        # First call — seeds the cache and creates the Lock.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(worker).result(timeout=5.0)

        # Expire the cache so the second call forces a refresh.
        key = ("google", "alice@example.com")
        if key in _cache:
            _cache[key].expires_at = 0.0  # force expired

        # Second call — without the fix, the Lock created above is bound
        # to a different event loop and raises RuntimeError.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(worker).result(timeout=5.0)

        assert not errors, f"Unexpected errors: {errors}"
        assert len(tokens) == 2
        assert tokens[0] == "BEARER"
        assert tokens[1] == "BEARER"

    def test_persistent_loop_is_singleton(self):
        """The persistent loop module must return the SAME loop object on
        repeated calls — a new loop per call would reintroduce the bug."""
        from gaia.connectors._loop import _get_persistent_loop

        loop1 = _get_persistent_loop()
        loop2 = _get_persistent_loop()
        assert loop1 is loop2, "Expected a singleton loop; got two different objects"
        assert loop1.is_running(), "Persistent loop must be running"


# -------------------------------------------------------------------------
# AC3: bounded wait — slow async op raises actionable error
# -------------------------------------------------------------------------


class TestBoundedWait:
    """AC3: a stalled async op must surface an actionable ConnectorsError
    within the configured timeout, not hang forever."""

    def test_run_sync_raises_on_timeout(self):
        """Patch the persistent loop's run_sync directly to inject a slow
        coroutine; verify ConnectorsError is raised within ~timeout."""
        from gaia.connectors._loop import run_sync

        async def slow_op():
            await asyncio.sleep(60)  # much longer than any test timeout
            return "never"

        timeout = 0.3  # seconds — fast enough for a test, detectable as timeout

        start = time.monotonic()
        with pytest.raises(ConnectorsError, match="timed out"):
            run_sync(slow_op(), timeout=timeout)
        elapsed = time.monotonic() - start

        # Should raise well within 3× the timeout, not hang.
        assert elapsed < timeout * 5, f"Took {elapsed:.2f}s — likely hung"

    def test_run_sync_returns_normally_when_fast(self):
        """A fast coroutine completes normally."""
        from gaia.connectors._loop import run_sync

        async def fast_op():
            return "done"

        result = run_sync(fast_op())
        assert result == "done"


# -------------------------------------------------------------------------
# AC4: contextvar preserved through the loop bridge
# -------------------------------------------------------------------------


class TestContextvarPreserved:
    """AC4: the agent-id contextvar set in the caller thread must be visible
    inside the async coroutine running on the persistent loop thread.

    run_coroutine_threadsafe does NOT inherit the caller's contextvars by
    default; the bridge must explicitly carry them via copy_context().
    """

    def test_contextvar_visible_inside_bridge(self):
        """Submit a coroutine via run_sync from a thread with a contextvar
        set; assert the coroutine sees the contextvar value."""
        from gaia.connectors._loop import run_sync

        captured: list[Optional[str]] = []

        async def read_ctx():
            captured.append(current_agent_id())

        _var_token = None

        def worker():
            with _agent_context("test:agent-42"):
                run_sync(read_ctx())

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5.0)
        assert not t.is_alive(), "worker thread timed out"

        assert captured == [
            "test:agent-42"
        ], f"Expected ['test:agent-42'], got {captured!r}"

    @respx.mock
    def test_contextvar_through_token_sync_wrapper(self, seeded):
        """End-to-end: agent-id contextvar is preserved when calling
        get_access_token_sync from a ThreadPoolExecutor worker.

        The test verifies the contextvar flows through the persistent-loop
        bridge by patching get_or_refresh at the api module's import site
        (where it is bound as a local name) so the spy runs on the loop
        thread and can read the contextvar there.
        """
        respx.post("https://oauth2.googleapis.com/token").mock(return_value=_ok_token())
        grant_agent("google", "builtin:chat", ["gmail.readonly"])

        observed_in_refresh: list[Optional[str]] = []

        import gaia.connectors.api as _api_mod
        import gaia.connectors.tokens as _tokens_mod

        original_get_or_refresh = _tokens_mod.get_or_refresh

        async def spy_get_or_refresh(*args, **kwargs):
            # This runs on the persistent loop thread; the contextvar was
            # copied from the caller thread by run_sync → _ctx_wrap.
            observed_in_refresh.append(current_agent_id())
            return await original_get_or_refresh(*args, **kwargs)

        results: list[str] = []
        errors: list[Exception] = []

        def worker():
            # Patch at the api module's import site so get_access_token
            # (which does `from gaia.connectors.tokens import get_or_refresh`)
            # actually sees the spy.
            with patch.object(_api_mod, "get_or_refresh", spy_get_or_refresh):
                with _agent_context("builtin:chat"):
                    try:
                        tok = get_access_token_sync(
                            provider="google", scopes=["gmail.readonly"]
                        )
                        results.append(tok)
                    except Exception as exc:
                        errors.append(exc)

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(worker).result(timeout=5.0)

        assert not errors, f"Unexpected errors: {errors}"
        assert results == ["BEARER"]
        # The contextvar must have been visible in the async refresh.
        assert (
            observed_in_refresh and observed_in_refresh[0] == "builtin:chat"
        ), f"Expected contextvar 'builtin:chat' in refresh, got {observed_in_refresh!r}"


# -------------------------------------------------------------------------
# AC5: loop-thread deadlock guard
# -------------------------------------------------------------------------


class TestLoopThreadDeadlockGuard:
    """run_sync must not deadlock when called from the persistent loop's own
    thread (which would cause .result() to block the loop forever)."""

    def test_run_sync_raises_if_called_from_loop_thread(self):
        """Submitting run_sync from inside the persistent loop's own thread
        raises RuntimeError (deadlock guard) rather than hanging."""
        from gaia.connectors._loop import _get_persistent_loop, run_sync

        errors: list[Exception] = []
        done = threading.Event()

        def run_from_loop_thread():
            try:

                async def dummy():
                    return "x"

                coro = dummy()
                try:
                    run_sync(coro)
                except RuntimeError:
                    # Expected: close the coroutine to avoid ResourceWarning.
                    coro.close()
                    return
                errors.append(AssertionError("Should have raised RuntimeError"))
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()

        # Schedule run_from_loop_thread to execute ON the persistent loop's thread.
        loop = _get_persistent_loop()
        loop.call_soon_threadsafe(run_from_loop_thread)
        done.wait(timeout=5.0)

        assert not errors, f"Unexpected errors: {errors}"
