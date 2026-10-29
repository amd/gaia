# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Web search survives DuckDuckGo turning automated searches away.

DDG answers bursts of automated searches with HTTP 202 and a bot-check page.
Before, one such answer ended web search for the rest of a research task, and
the error was stored into memory as a "known error" to avoid. These tests pin:

* retries with jittered exponential backoff that honours ``Retry-After``;
* the lite endpoint is tried when the html one refuses;
* one process-wide pacer, so parallel searches do not burst;
* the tool tells the model search is temporarily unavailable, flagged so the
  loop waits instead of counting a failure, until a streak shows it is dead;
* a configured Tavily connector answers when DDG will not;
* a rate limit is never stored as memory knowledge.

No test touches the network: every HTTP response is a stub.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.turn_scope import TurnScopeGuard, _is_tool_failure
from gaia.agents.tools import browser_tools
from gaia.agents.tools.browser_tools import (
    SEARCH_UNAVAILABLE_STREAK_LIMIT,
    BrowserToolsMixin,
)
from gaia.tool_cancellation import ToolCancelled
from gaia.web import client as web_client
from gaia.web.client import SearchUnavailableError, WebClient, _SearchGate

pytest.importorskip("bs4")

HTML_PAGE = """
<html><body>
<div class="result">
  <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">
    Html Hit</a>
  <a class="result__snippet">Html snippet about anomaly detection.</a>
</div>
</body></html>
"""

LITE_PAGE = """
<html><body><table>
<tr><td>1.</td><td><a rel="nofollow" class="result-link"
  href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Flite">Lite Hit</a></td></tr>
<tr><td></td><td class="result-snippet">Lite snippet.</td></tr>
<tr><td>2.</td><td><a rel="nofollow" class="result-link"
  href="https://example.net/second">Second Lite</a></td></tr>
</table></body></html>
"""

CHALLENGE_PAGE = (
    '<html><body><div class="anomaly-modal__title">'
    "Unfortunately, bots use DuckDuckGo too.</div></body></html>"
)


def _response(status, text="<html><body></body></html>", headers=None):
    response = MagicMock()
    response.status_code = status
    response.text = text
    response.headers = {"Content-Type": "text/html", **(headers or {})}
    response.encoding = "utf-8"
    response.apparent_encoding = "utf-8"
    return response


@pytest.fixture
def sleeps(monkeypatch):
    """A fresh process-wide gate that records its waits instead of sleeping."""
    slept = []
    clock = {"now": 1000.0}

    def fake_sleep(seconds):
        slept.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(web_client.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        web_client, "_SEARCH_GATE", _SearchGate(min_interval=0.0, sleep=fake_sleep)
    )
    monkeypatch.setattr(web_client.random, "uniform", lambda a, b: 0.0)
    return slept


@pytest.fixture
def client():
    c = WebClient()
    yield c
    c.close()


def _serve(client, *responses):
    """Stub the HTTP layer; record (method, url, data) of each request."""
    calls = []
    queue = list(responses)

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs.get("data")))
        return queue.pop(0)

    patcher = patch.object(client, "_request", side_effect=fake_request)
    return patcher, calls


class TestRetryAndEndpoints:
    def test_html_refusal_falls_through_to_the_lite_endpoint(self, client, sleeps):
        patcher, calls = _serve(client, _response(202), _response(200, LITE_PAGE))
        with patcher:
            results = client.search_duckduckgo("gaia amd", num_results=5)

        assert [c[1] for c in calls] == [
            "https://html.duckduckgo.com/html/",
            "https://lite.duckduckgo.com/lite/",
        ]
        assert all(c[0] == "POST" and c[2]["q"] == "gaia amd" for c in calls)
        assert results == [
            {
                "title": "Lite Hit",
                "url": "https://example.org/lite",
                "snippet": "Lite snippet.",
            },
            {
                "title": "Second Lite",
                "url": "https://example.net/second",
                "snippet": "",
            },
        ]
        assert sleeps == []

    def test_an_endpoint_answering_after_a_refusal_is_logged(
        self, client, sleeps, caplog
    ):
        patcher, _ = _serve(client, _response(202), _response(200, LITE_PAGE))
        with patcher, caplog.at_level(logging.WARNING):
            client.search_duckduckgo("q")
        assert "lite endpoint answered after an earlier refusal (HTTP 202)" in (
            caplog.text
        )

    def test_stop_ends_the_backoff_without_another_request(self, client, sleeps):
        patcher, calls = _serve(client, _response(202), _response(202))
        with patcher, pytest.raises(ToolCancelled, match="stopped"):
            # Stop arrives once the first backoff wait has begun.
            client.search_duckduckgo("q", should_stop=lambda: bool(sleeps))
        assert len(calls) == 2
        assert sleeps == [web_client._SearchGate.STOP_POLL_SECONDS]

    def test_backs_off_exponentially_then_succeeds(self, client, sleeps):
        patcher, calls = _serve(
            client,
            _response(202),
            _response(202),
            _response(429),
            _response(202),
            _response(200, HTML_PAGE),
        )
        with patcher:
            results = client.search_duckduckgo("q")

        assert len(calls) == 5
        assert results[0]["url"] == "https://example.com/a"
        assert sleeps == [2.0, 4.0]

    def test_jitter_is_added_to_the_backoff(self, client, sleeps, monkeypatch):
        monkeypatch.setattr(web_client.random, "uniform", lambda a, b: b)
        patcher, _ = _serve(
            client, _response(202), _response(202), _response(200, HTML_PAGE)
        )
        with patcher:
            client.search_duckduckgo("q")
        assert sleeps == [4.0]

    def test_gives_up_after_max_attempts_with_a_retry_hint(self, client, sleeps):
        patcher, calls = _serve(client, *[_response(202) for _ in range(6)])
        with patcher, pytest.raises(SearchUnavailableError) as info:
            client.search_duckduckgo("q", max_attempts=3)

        assert len(calls) == 6
        assert sleeps == [2.0, 4.0]
        assert info.value.status == 202
        assert info.value.retry_after == pytest.approx(8.0)
        assert "HTTP 202" in str(info.value)
        # The next search waits the block out rather than hammering DDG.
        assert web_client._SEARCH_GATE.blocked_for() == pytest.approx(8.0)

    def test_retry_after_is_honoured(self, client, sleeps):
        patcher, _ = _serve(
            client,
            _response(429, headers={"Retry-After": "7"}),
            _response(429),
            _response(200, HTML_PAGE),
        )
        with patcher:
            client.search_duckduckgo("q")
        assert sleeps == [7.0]

    def test_retry_after_as_an_http_date(self, client, sleeps):
        when = datetime.now(timezone.utc) + timedelta(seconds=11)
        response = _response(429, headers={"Retry-After": format_datetime(when, True)})
        assert 9.0 <= web_client._retry_after_seconds(response) <= 11.0

    def test_a_retry_after_beyond_the_cap_is_returned_not_slept(self, client, sleeps):
        patcher, calls = _serve(
            client, _response(429, headers={"Retry-After": "120"}), _response(429)
        )
        with patcher, pytest.raises(SearchUnavailableError) as info:
            client.search_duckduckgo("q")
        assert len(calls) == 2
        assert sleeps == []
        assert info.value.retry_after == 120.0

    def test_an_active_long_block_fails_fast_without_a_request(self, client, sleeps):
        web_client._SEARCH_GATE.back_off(60)
        patcher, calls = _serve(client)
        with patcher, pytest.raises(SearchUnavailableError):
            client.search_duckduckgo("q")
        assert calls == []

    def test_a_bot_check_page_with_200_is_a_block(self, client, sleeps):
        patcher, calls = _serve(
            client, _response(200, CHALLENGE_PAGE), _response(200, LITE_PAGE)
        )
        with patcher:
            results = client.search_duckduckgo("q")
        assert len(calls) == 2
        assert results[0]["title"] == "Lite Hit"

    def test_results_that_mention_anomaly_are_not_a_block(self, client, sleeps):
        patcher, calls = _serve(client, _response(200, HTML_PAGE))
        with patcher:
            results = client.search_duckduckgo("anomaly detection")
        assert len(calls) == 1 and results[0]["title"] == "Html Hit"

    def test_other_failures_are_not_retried(self, client, sleeps):
        patcher, calls = _serve(client, _response(500))
        with patcher, pytest.raises(RuntimeError, match="HTTP 500") as info:
            client.search_duckduckgo("q")
        assert not isinstance(info.value, SearchUnavailableError)
        assert len(calls) == 1

    def test_max_attempts_must_be_positive(self, client, sleeps):
        with pytest.raises(ValueError, match="max_attempts"):
            client.search_duckduckgo("q", max_attempts=0)


class TestSearchGate:
    def test_back_to_back_searches_are_spaced(self, monkeypatch):
        slept = []
        clock = {"now": 50.0}
        monkeypatch.setattr(web_client.time, "monotonic", lambda: clock["now"])
        gate = _SearchGate(min_interval=1.5, sleep=slept.append)

        gate.wait_turn()
        gate.wait_turn()
        gate.wait_turn()

        # Each caller reserves the next slot, so parallel callers stagger too.
        assert slept == [1.5, 3.0]

    def test_a_block_pushes_every_caller_back(self, monkeypatch):
        slept = []
        monkeypatch.setattr(web_client.time, "monotonic", lambda: 10.0)
        gate = _SearchGate(min_interval=0.0, sleep=slept.append)
        gate.back_off(5.0)
        assert gate.blocked_for() == 5.0
        gate.wait_turn()
        assert slept == [5.0]

    def test_a_stop_check_slices_the_wait_and_ends_it(self, monkeypatch):
        slept = []
        monkeypatch.setattr(web_client.time, "monotonic", lambda: 10.0)
        gate = _SearchGate(min_interval=0.0, sleep=slept.append)
        gate.back_off(30.0)

        with pytest.raises(ToolCancelled):
            gate.wait_turn(should_stop=lambda: len(slept) >= 2)
        assert slept == [0.25, 0.25]

    def test_a_stop_check_that_never_fires_waits_the_full_time(self, monkeypatch):
        slept = []
        monkeypatch.setattr(web_client.time, "monotonic", lambda: 10.0)
        gate = _SearchGate(min_interval=0.0, sleep=slept.append)
        gate.back_off(1.0)
        gate.wait_turn(should_stop=lambda: False)
        assert sum(slept) == pytest.approx(1.0) and max(slept) <= 0.25


def _search_tool(web):
    tools = {}

    class _Host(BrowserToolsMixin):
        def __init__(self, web_client):
            self._web_client = web_client
            self._path_validator = None
            self._tools = {}

    def capture(atomic=True):
        def decorator(func):
            tools[func.__name__] = func
            return func

        return decorator

    host = _Host(web)
    with patch("gaia.agents.base.tools.tool", capture):
        host.register_browser_tools()
    return host, tools["search_web"]


def _unavailable():
    return SearchUnavailableError(
        "DuckDuckGo turned the search away (HTTP 202)", retry_after=8.0, status=202
    )


@pytest.fixture
def no_tavily(monkeypatch):
    monkeypatch.setattr(
        "gaia.connectors.mcp_server.is_mcp_server_configured", lambda _id: False
    )


class TestSearchToolResult:
    def test_unavailable_search_is_a_throttle_not_a_failure(self, no_tavily):
        web = MagicMock()
        web.search_duckduckgo.side_effect = _unavailable()
        _, search_web = _search_tool(web)

        result = search_web("ryzen ai npu")

        assert result["rate_limited"] is True
        assert result["retryable"] is True
        assert result["wait_time_seconds"] == 8.0
        assert "temporarily unavailable" in result["error"]
        assert "HTTP 202" in result["error"]
        assert "fetch_page" in result["hint"] and "narrower" in result["hint"]

        guard = TurnScopeGuard(failure_limit=2)
        for _ in range(3):
            guard.record("search_web", {"query": "x"}, result)
        assert not _is_tool_failure(result)
        assert guard.failures == {}

    def test_a_streak_of_refusals_lets_the_breaker_count_it(self, no_tavily):
        web = MagicMock()
        web.search_duckduckgo.side_effect = _unavailable()
        _, search_web = _search_tool(web)

        results = [search_web(f"q{i}") for i in range(SEARCH_UNAVAILABLE_STREAK_LIMIT)]

        assert all(r.get("rate_limited") for r in results[:-1])
        last = results[-1]
        assert "rate_limited" not in last and last["retryable"] is False
        assert "stop calling search_web" in last["error"]
        assert _is_tool_failure(last)
        # With no backup provider, the user is told how to add one.
        assert "gaia connectors configure mcp-tavily" in last["hint"]

    def test_stop_during_a_search_wait_ends_the_search(self, no_tavily):
        web = MagicMock()

        def refuse_until_stopped(query, num_results, should_stop):
            assert should_stop()
            raise ToolCancelled("web search was stopped while waiting")

        web.search_duckduckgo.side_effect = refuse_until_stopped
        host, search_web = _search_tool(web)
        host._cancel_event = threading.Event()
        host._cancel_event.set()

        result = search_web("q")

        assert result.startswith("Web search stopped")
        assert getattr(host, "_search_unavailable_streak", 0) == 0

    def test_ui_stop_is_seen_by_the_search(self, no_tavily):
        web = MagicMock()
        seen = []
        web.search_duckduckgo.side_effect = lambda q, num_results, should_stop: (
            seen.append(should_stop()) or []
        )
        host, search_web = _search_tool(web)
        host.console = MagicMock(cancelled=threading.Event())
        search_web("q")
        host.console.cancelled.set()
        search_web("q")
        assert seen == [False, True]

    def test_a_successful_search_resets_the_streak(self, no_tavily):
        web = MagicMock()
        _, search_web = _search_tool(web)
        for _ in range(SEARCH_UNAVAILABLE_STREAK_LIMIT - 1):
            web.search_duckduckgo.side_effect = _unavailable()
            search_web("q")
        web.search_duckduckgo.side_effect = None
        web.search_duckduckgo.return_value = [
            {"title": "T", "url": "https://example.com", "snippet": ""}
        ]
        assert "1. T" in search_web("q")

        web.search_duckduckgo.side_effect = _unavailable()
        assert search_web("q")["rate_limited"] is True

    def test_configured_tavily_answers_when_ddg_refuses(self, monkeypatch):
        monkeypatch.setattr(
            "gaia.connectors.mcp_server.is_mcp_server_configured",
            lambda connector_id: connector_id == "mcp-tavily",
        )
        tavily = MagicMock(configured=True)
        tavily.search.return_value = {
            "results": [
                {"title": "Tav", "url": "https://tavily.example/x", "content": "c"}
            ]
        }
        monkeypatch.setattr("gaia.web.tavily.TavilyClient", lambda: tavily)
        web = MagicMock()
        web.search_duckduckgo.side_effect = _unavailable()
        _, search_web = _search_tool(web)

        result = search_web("q", num_results=3)

        tavily.search.assert_called_once_with("q", max_results=3)
        tavily.close.assert_called_once()
        assert "Tavily" in result and "1. Tav" in result
        assert "https://tavily.example/x" in result.splitlines()[4]

    def test_switching_to_tavily_is_logged(self, monkeypatch, caplog):
        monkeypatch.setattr(
            "gaia.connectors.mcp_server.is_mcp_server_configured", lambda _id: True
        )
        tavily = MagicMock(configured=True)
        tavily.search.return_value = {
            "results": [{"title": "Tav", "url": "https://t.example", "content": ""}]
        }
        monkeypatch.setattr("gaia.web.tavily.TavilyClient", lambda: tavily)
        web = MagicMock()
        web.search_duckduckgo.side_effect = _unavailable()
        _, search_web = _search_tool(web)

        with caplog.at_level(logging.WARNING):
            search_web("q")

        assert "answered by the Tavily connector instead (1 results)" in caplog.text

    def test_a_failing_tavily_is_reported_not_hidden(self, monkeypatch):
        monkeypatch.setattr(
            "gaia.connectors.mcp_server.is_mcp_server_configured", lambda _id: True
        )
        tavily = MagicMock(configured=True)
        tavily.search.side_effect = RuntimeError("credit cap reached")
        monkeypatch.setattr("gaia.web.tavily.TavilyClient", lambda: tavily)
        web = MagicMock()
        web.search_duckduckgo.side_effect = _unavailable()
        _, search_web = _search_tool(web)

        result = search_web("q")

        assert result["rate_limited"] is True
        assert "Tavily connector also failed: credit cap reached" in result["error"]
        for _ in range(SEARCH_UNAVAILABLE_STREAK_LIMIT - 1):
            last = search_web("q")
        # Tavily is set up, so the user is not told to set it up.
        assert "configure mcp-tavily" not in last["hint"]

    def test_no_tavily_means_no_tavily_client(self, no_tavily, monkeypatch):
        built = MagicMock()
        monkeypatch.setattr("gaia.web.tavily.TavilyClient", built)
        assert browser_tools._tavily_fallback("q", 5) == (None, None)
        built.assert_not_called()


class _MemoryHost(MemoryMixin):
    def __init__(self):
        self._memory_store = MagicMock()
        self._memory_context = "global"

    def _automatic_writes_off(self):
        return False

    def _embed_and_index(self, *args):
        return None


class TestRateLimitsAreNotRemembered:
    def test_a_rate_limited_result_is_not_stored(self):
        host = _MemoryHost()
        result = {"status": "error", "error": "search refused", "rate_limited": True}
        host._auto_store_error("search_web", result["error"], {"query": "q"}, result)
        host._memory_store.store.assert_not_called()

    @pytest.mark.parametrize(
        "message",
        [
            "Error performing web search: DuckDuckGo answered HTTP 202, so no "
            "results were read. It may be rate-limiting automated searches",
            "HTTP 429 Too Many Requests",
            "API rate-limited this client",
        ],
    )
    def test_rate_limit_wordings_are_transient(self, message):
        assert MemoryMixin._is_transient_error(message)
        host = _MemoryHost()
        host._auto_store_error("search_web", message, {"query": "q"}, message)
        host._memory_store.store.assert_not_called()

    def test_a_real_error_is_still_stored(self):
        host = _MemoryHost()
        host._auto_store_error("search_web", "Invalid query syntax", {"query": "q"})
        host._memory_store.store.assert_called_once()
