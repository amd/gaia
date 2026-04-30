# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-X1-bridge: sync→async bridge under ``ThreadPoolExecutor``.

Per plan amendment A15, this test must explicitly use
``ThreadPoolExecutor`` because that's the production path:

  Agent.process_query (sync, ThreadPoolExecutor worker)
    └─→ tool body
        └─→ get_access_token_sync(...)            # sync
            └─→ asyncio.run(get_access_token(...)) # async
                └─→ tokens.get_or_refresh
                    └─→ httpx.AsyncClient

The contextvar set by ``Agent.process_query`` (via ``_agent_context``) must
flow through ``asyncio.run``'s ``contextvars.copy_context()`` to the async
side. Tests that call ``get_access_token_sync`` from the main thread are
not exercising the production bridge.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import respx

from gaia.connections import (
    AuthRequiredError,
    get_access_token_sync,
    grant_agent,
)
from gaia.connections.context import _agent_context, current_agent_id
from gaia.connections.providers import _registry
from gaia.connections.store import save_connection


@pytest.fixture
def google_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    monkeypatch.setattr("gaia.connections.grants.Path.home", lambda: tmp_path)
    _registry.clear()
    from gaia.connections.providers import get as get_provider

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


class TestThreadPoolBridge:
    """The agent runtime runs ``process_query`` in a ThreadPoolExecutor
    worker; the contextvar set inside that worker must propagate into the
    inner ``asyncio.run`` context."""

    @respx.mock
    def test_contextvar_propagates_via_asyncio_run(self, seeded):
        respx.post("https://oauth2.googleapis.com/token").mock(return_value=_ok_token())
        grant_agent("google", "builtin:chat", ["gmail.readonly"])

        results: dict = {}

        def worker():
            with _agent_context("builtin:chat"):
                # Sanity: the ctx is set in this thread.
                results["before"] = current_agent_id()
                results["token"] = get_access_token_sync(
                    provider="google", scopes=["gmail.readonly"]
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            pool.submit(worker).result(timeout=5.0)

        assert results["before"] == "builtin:chat"
        assert results["token"] == "BEARER"

    @respx.mock
    def test_no_grant_raises_in_thread_pool(self, seeded):
        # Same setup but no grant for builtin:chat.
        respx.post("https://oauth2.googleapis.com/token").mock(return_value=_ok_token())

        captured = {}

        def worker():
            with _agent_context("builtin:chat"):
                try:
                    get_access_token_sync(provider="google", scopes=["gmail.readonly"])
                except AuthRequiredError as e:
                    captured["err"] = e

        with ThreadPoolExecutor(max_workers=2) as pool:
            pool.submit(worker).result(timeout=5.0)

        err = captured.get("err")
        assert err is not None
        assert err.reason is AuthRequiredError.Reason.AGENT_NOT_GRANTED
        assert err.agent_id == "builtin:chat"
        assert err.provider == "google"

    @respx.mock
    def test_kwarg_overrides_contextvar(self, seeded):
        # Plan: kwarg agent_id wins over the contextvar (explicit over implicit).
        respx.post("https://oauth2.googleapis.com/token").mock(return_value=_ok_token())
        grant_agent("google", "explicit:agent", ["gmail.readonly"])

        results = {}

        def worker():
            with _agent_context("builtin:chat"):
                # Pass an explicit different agent_id — it must win.
                results["token"] = get_access_token_sync(
                    provider="google",
                    scopes=["gmail.readonly"],
                    agent_id="explicit:agent",
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            pool.submit(worker).result(timeout=5.0)

        assert results["token"] == "BEARER"


class TestThreadIsolation:
    """A15: contextvar must not leak across threads — a worker that did
    NOT enter ``_agent_context`` sees ``current_agent_id() is None``."""

    def test_worker_without_context_sees_none(self):
        observed: list = []

        def child():
            observed.append(current_agent_id())

        with _agent_context("builtin:chat"):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(child).result(timeout=2.0)

        assert observed == [None]


class TestSequentialAgentInvocations:
    """
    Two sequential agent invocations through the sync→async bridge each
    return a valid token, and the second uses the in-thread cache when
    the first thread's token is still valid.

    Cross-thread *concurrent* refresh is an explicit non-guarantee in v1:
    AC6 ("N concurrent calls = 1 refresh round-trip") is scoped to a
    single ``asyncio`` event loop, because ``asyncio.Lock`` is per-loop.
    Multiple threads each running ``asyncio.run`` will each create their
    own event loop and may each fire a refresh round-trip independently
    — correct but not optimal. See ``docs/security/connections.mdx``
    "Cross-process / cross-thread races".
    """

    @respx.mock
    def test_two_sequential_invocations_in_thread_pool(self, seeded):
        respx.post("https://oauth2.googleapis.com/token").mock(return_value=_ok_token())
        grant_agent("google", "builtin:chat", ["gmail.readonly"])

        def worker():
            with _agent_context("builtin:chat"):
                return get_access_token_sync(
                    provider="google", scopes=["gmail.readonly"]
                )

        with ThreadPoolExecutor(max_workers=1) as pool:
            tok1 = pool.submit(worker).result(timeout=5.0)
            tok2 = pool.submit(worker).result(timeout=5.0)

        assert tok1 == "BEARER"
        assert tok2 == "BEARER"
