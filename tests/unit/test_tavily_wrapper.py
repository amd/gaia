# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the Tavily wrapper (gaia.web.tavily).

The ``tavily-python`` SDK is mocked end-to-end via an injected fake client, so
these tests never touch the network or require the SDK to be installed.
"""

import logging

import pytest

import gaia.web.tavily as tav
from gaia.web.tavily import (
    AsyncTavilyClient,
    BudgetConfig,
    TavilyBudgetExceeded,
    TavilyClient,
    TavilyConfigError,
)

# --- Test doubles -----------------------------------------------------------


class FakeSyncSDK:
    """Stand-in for tavily.TavilyClient with call counters."""

    def __init__(self, response=None):
        self.response = response or {"results": [{"title": "t", "url": "u"}]}
        self.search_calls = 0
        self.extract_calls = 0

    def search(self, **kwargs):
        self.search_calls += 1
        return {**self.response, "query": kwargs.get("query")}

    def extract(self, **kwargs):
        self.extract_calls += 1
        return {**self.response, "urls": kwargs.get("urls")}


class FakeAsyncSDK:
    """Stand-in for tavily.AsyncTavilyClient."""

    def __init__(self, response=None):
        self.response = response or {"results": [{"title": "t", "url": "u"}]}
        self.search_calls = 0
        self.closed = False

    async def search(self, **kwargs):
        self.search_calls += 1
        return {**self.response, "query": kwargs.get("query")}

    async def aclose(self):
        self.closed = True


class FakeAsyncSDKClass:
    """Stand-in for tavily.AsyncTavilyClient as the ``_SDK_CLASS``.

    Unlike ``FakeAsyncSDK`` (injected ready-made), this is instantiated by the
    wrapper itself via ``_SDK_CLASS(api_key=...)``, so it must accept the key.
    """

    def __init__(self, api_key=None):
        self.api_key = api_key
        self.search_calls = 0

    async def search(self, **kwargs):
        self.search_calls += 1
        return {"results": [], "query": kwargs.get("query")}

    async def aclose(self):
        pass


class FakeWeb:
    """Stand-in for WebClient providing the DuckDuckGo fallback."""

    def __init__(self):
        self.calls = 0

    def search_duckduckgo(self, query, num_results=5):
        self.calls += 1
        return [{"title": "DDG", "url": "http://x", "snippet": "snip"}]

    def close(self):
        pass


class Clock:
    """Controllable replacement for the module's ``time`` reference."""

    def __init__(self, t=1000.0):
        self.t = t

    def time(self):
        return self.t


@pytest.fixture
def fake_sdk():
    return FakeSyncSDK()


def make_client(fake_sdk, **kwargs):
    """Configured sync client backed by an in-memory DB and the fake SDK."""
    return TavilyClient(db_path=":memory:", sdk_client=fake_sdk, **kwargs)


# --- Caching ----------------------------------------------------------------


def test_search_returns_sdk_response(fake_sdk):
    client = make_client(fake_sdk)
    result = client.search("hello")
    assert result["query"] == "hello"
    assert fake_sdk.search_calls == 1
    client.close()


def test_cache_hit_skips_second_sdk_call(fake_sdk):
    client = make_client(fake_sdk)
    client.search("AMD ROCm latest version")
    # Same query, only case/whitespace differ → normalized to the same key.
    client.search("amd   rocm  LATEST version ")
    assert fake_sdk.search_calls == 1
    client.close()


def test_cache_key_includes_params(fake_sdk):
    client = make_client(fake_sdk)
    client.search("q", max_results=5)
    client.search("q", max_results=10)  # different param → different request
    assert fake_sdk.search_calls == 2
    client.close()


def test_cache_expires_after_ttl(fake_sdk, monkeypatch):
    clock = Clock(1000.0)
    monkeypatch.setattr(tav, "time", clock)
    client = make_client(fake_sdk, cache_ttl=60)

    client.search("q")
    assert fake_sdk.search_calls == 1

    clock.t = 1000.0 + 61  # advance past the TTL
    client.search("q")
    assert fake_sdk.search_calls == 2  # stale → re-fetched
    client.close()


# --- Credit ledger ----------------------------------------------------------


def test_ledger_tracks_credits_by_depth(fake_sdk):
    client = make_client(fake_sdk)
    client.search("a", search_depth="advanced")  # 2 credits
    client.search("b", search_depth="basic")  # 1 credit
    usage = client.usage()
    assert usage["total_credits"] == 3
    assert usage["by_operation"]["search"]["calls"] == 2
    client.close()


def test_credits_read_from_response_usage():
    sdk = FakeSyncSDK(response={"results": [], "usage": {"credits": 7}})
    client = make_client(sdk)
    client.search("q")
    assert client.usage()["total_credits"] == 7
    client.close()


def test_cached_calls_are_not_remetered(fake_sdk):
    client = make_client(fake_sdk)
    client.search("q")
    client.search("q")  # cache hit
    assert client.usage()["total_credits"] == 1  # charged once, not twice
    client.close()


# --- Budget gate ------------------------------------------------------------


def test_budget_warns_near_threshold(fake_sdk, caplog):
    client = make_client(fake_sdk, budget=BudgetConfig(cap=10, warn_threshold=0.8))
    client.insert(
        "tavily_ledger", {"operation": "search", "credits": 8, "created_at": 0}
    )
    with caplog.at_level(logging.WARNING, logger="gaia.web.tavily"):
        client.search("q")  # projected 9/10 → past 80%
    assert "budget warning" in caplog.text.lower()
    assert fake_sdk.search_calls == 1  # warned but proceeded
    client.close()


def test_budget_blocks_over_cap(fake_sdk):
    client = make_client(fake_sdk, budget=BudgetConfig(cap=0))
    with pytest.raises(TavilyBudgetExceeded):
        client.search("q")
    assert fake_sdk.search_calls == 0  # blocked before spending
    assert client.usage()["total_credits"] == 0
    client.close()


def test_budget_warn_only_mode_does_not_block(fake_sdk, caplog):
    client = make_client(fake_sdk, budget=BudgetConfig(cap=0, block=False))
    with caplog.at_level(logging.WARNING, logger="gaia.web.tavily"):
        result = client.search("q")  # over cap but warn-only
    assert result["query"] == "q"
    assert fake_sdk.search_calls == 1
    assert "warn-only" in caplog.text.lower()
    client.close()


def test_unlimited_budget_never_blocks(fake_sdk):
    client = make_client(fake_sdk, budget=BudgetConfig(cap=None))
    for i in range(10):
        client.search(f"q{i}")
    assert fake_sdk.search_calls == 10
    client.close()


# --- DuckDuckGo fallback ----------------------------------------------------


def test_unconfigured_search_falls_back_to_ddg(monkeypatch):
    monkeypatch.setattr(tav, "_load_api_key", lambda: None)
    web = FakeWeb()
    client = TavilyClient(db_path=":memory:", web_client=web)
    assert client.configured is False

    result = client.search("anything")
    assert result["source"] == "duckduckgo"
    assert result["results"][0]["url"] == "http://x"
    assert web.calls == 1
    assert client.usage()["total_credits"] == 0  # DDG is free → no ledger entry
    client.close()


def test_unconfigured_extract_raises(monkeypatch):
    monkeypatch.setattr(tav, "_load_api_key", lambda: None)
    client = TavilyClient(db_path=":memory:", web_client=FakeWeb())
    with pytest.raises(TavilyConfigError):
        client.extract("http://example.com")
    client.close()


def test_configured_but_sdk_missing_raises(monkeypatch):
    # Connector configured (key present) but tavily-python not installed.
    monkeypatch.setattr(TavilyClient, "_SDK_CLASS", None)
    with pytest.raises(TavilyConfigError, match="tavily-python"):
        TavilyClient(db_path=":memory:", api_key="tvly-xxx")


# --- Async client -----------------------------------------------------------


async def test_async_search_caches():
    sdk = FakeAsyncSDK()
    client = AsyncTavilyClient(db_path=":memory:", sdk_client=sdk)
    await client.search("q")
    await client.search("q")  # cache hit
    assert sdk.search_calls == 1
    client.close()


async def test_async_unconfigured_falls_back_to_ddg(monkeypatch):
    async def no_key():
        return None

    monkeypatch.setattr(tav, "_load_api_key_async", no_key)
    web = FakeWeb()
    client = AsyncTavilyClient(db_path=":memory:", web_client=web)
    result = await client.search("anything")
    assert result["source"] == "duckduckgo"
    assert web.calls == 1
    client.close()


async def test_async_configured_construction_in_loop_does_not_raise(monkeypatch):
    """Regression: constructing the async client inside a running event loop with
    a configured connector must not raise.

    Pre-fix, ``__init__`` resolved the key synchronously via
    ``get_credential_sync()``, which raises ``RuntimeError`` inside a running
    loop. Resolution is now deferred to the async path on first use.
    """
    import gaia.connectors.handler as handler_mod
    import gaia.connectors.mcp_server as mcp_mod

    monkeypatch.setattr(mcp_mod, "is_mcp_server_configured", lambda _cid: True)

    async def fake_get_credential(_connector_id, **_kwargs):
        return {"env": {"TAVILY_API_KEY": "tvly-test"}}

    monkeypatch.setattr(handler_mod, "get_credential", fake_get_credential)
    monkeypatch.setattr(AsyncTavilyClient, "_SDK_CLASS", FakeAsyncSDKClass)

    # Construction must not raise inside the loop...
    client = AsyncTavilyClient(db_path=":memory:")
    # ...and the key resolves on first use via the async path.
    result = await client.search("q")
    assert client.configured is True
    assert result["query"] == "q"
    assert isinstance(client._sdk, FakeAsyncSDKClass)
    assert client._sdk.api_key == "tvly-test"
    await client.aclose()


def test_sync_context_manager_closes(fake_sdk):
    with make_client(fake_sdk) as client:
        client.search("q")
    assert client.db_ready is False  # __exit__ closed the cache DB


async def test_async_context_manager_awaits_sdk_aclose():
    sdk = FakeAsyncSDK()
    async with AsyncTavilyClient(db_path=":memory:", sdk_client=sdk) as client:
        await client.search("q")
    assert sdk.closed is True  # __aexit__ -> aclose() awaited the SDK
    assert client.db_ready is False
