# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for ``gaia.connectors.context`` — the agent-id contextvar plumbing.

Per A9 of the plan, ``_agent_context`` is **PRIVATE** (leading underscore,
not re-exported from the package). A malicious tool body cannot import it
to forge an agent identity. The agent runtime imports it via the private
path ``from gaia.connectors.context import _agent_context``.

``current_agent_id`` IS public — tools may read the current agent id but
not set it.
"""

from __future__ import annotations

import asyncio
import threading

from gaia.connectors.context import _agent_context, current_agent_id


class TestBasicSetAndRestore:
    def test_outside_context_returns_none(self):
        assert current_agent_id() is None

    def test_inside_context_returns_id(self):
        with _agent_context("builtin:chat"):
            assert current_agent_id() == "builtin:chat"

    def test_context_restored_on_exit(self):
        assert current_agent_id() is None
        with _agent_context("builtin:chat"):
            pass
        assert current_agent_id() is None

    def test_nested_contexts_restore_correctly(self):
        with _agent_context("builtin:chat"):
            assert current_agent_id() == "builtin:chat"
            with _agent_context("custom:abc:inbox"):
                assert current_agent_id() == "custom:abc:inbox"
            # Outer context is preserved on inner-block exit.
            assert current_agent_id() == "builtin:chat"
        assert current_agent_id() is None

    def test_exception_in_block_still_restores_context(self):
        try:
            with _agent_context("builtin:chat"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert current_agent_id() is None


class TestNotPubliclyExported:
    """Per A9: only ``_agent_context`` (private) sets the contextvar; the
    package surface does NOT re-export it. A tool body that tries
    ``from gaia.connectors import agent_context`` fails."""

    def test_not_in_package_init(self):
        import gaia.connectors as conn

        assert not hasattr(conn, "agent_context")

    def test_not_in_api_module(self):
        from gaia.connectors import api

        assert not hasattr(api, "agent_context")

    def test_current_agent_id_is_public(self):
        # Reading is allowed; setting is private.
        import gaia.connectors.context as ctx

        assert hasattr(ctx, "current_agent_id")
        assert callable(ctx.current_agent_id)


class TestThreadIsolation:
    """ContextVars are thread-local in CPython. Verify that setting the
    context in the main thread does NOT leak into a worker thread that did
    not enter the context manager.
    """

    def test_contextvar_does_not_leak_across_threads(self):
        observed: list[str | None] = []

        def worker():
            observed.append(current_agent_id())

        with _agent_context("builtin:chat"):
            t = threading.Thread(target=worker)
            t.start()
            t.join()

        assert observed == [None]


class TestAsyncioPropagation:
    """``asyncio`` tasks inherit the parent's context (via copy_context).
    This is what makes the sync agent body → ``asyncio.run`` → async
    refresh path resolve agent_id from the contextvar.
    """

    async def test_context_propagates_to_async_task(self):
        observed: list[str | None] = []

        async def child():
            observed.append(current_agent_id())

        with _agent_context("builtin:chat"):
            await child()

        assert observed == ["builtin:chat"]

    def test_asyncio_run_inherits_caller_thread_context(self):
        # This mirrors the real sync→async bridge: agent runtime sets the
        # context, calls get_access_token_sync, which calls asyncio.run.
        # The new event loop must inherit the calling thread's contextvars.
        observed: list[str | None] = []

        async def fetch():
            observed.append(current_agent_id())

        with _agent_context("builtin:chat"):
            asyncio.run(fetch())

        assert observed == ["builtin:chat"]
