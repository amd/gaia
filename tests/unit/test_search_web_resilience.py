# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A long research turn keeps its web search (#7854).

DuckDuckGo refuses a burst of automated searches (measured: the 15th quick
search in ~35 s). These tests pin the three things that keep a 25-search turn
working with nothing configured, and the vendor-neutral connector fallback:

* repeated queries (case and spacing aside) are answered from a session cache;
* the process-wide gate paces searches below the burst instead of only backing
  off after a refusal, and Stop still ends a pacing wait;
* when DuckDuckGo refuses, ANY configured search connector answers (not only
  Tavily), and with none configured the model gets a bounded wait message
  that names Settings > Connectors.

No test touches the network.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.tools import browser_tools
from gaia.agents.tools.browser_tools import (
    SEARCH_CACHE_TTL_SECONDS,
    SEARCH_UNAVAILABLE_STREAK_LIMIT,
    BrowserToolsMixin,
    _parse_search_text,
)
from gaia.connectors.spec import WEB_SEARCH, ConnectorSpec
from gaia.mcp.client.mcp_client import MCPTool
from gaia.tool_cancellation import ToolCancelled
from gaia.web import client as web_client
from gaia.web.client import SearchUnavailableError, WebClient, _SearchGate

pytest.importorskip("bs4")

BRAVE = "mcp-brave-search"
TAVILY = "mcp-tavily"


def _page(query: str) -> str:
    slug = query.replace(" ", "-")
    return (
        '<html><body><div class="result">'
        f'<a class="result__a" href="https://example.com/{slug}">Hit {query}</a>'
        '<a class="result__snippet">snippet</a></div></body></html>'
    )


class _Clock:
    """Fake monotonic time; sleeping advances it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class _FakeDDG:
    """Answers until more than ``limit`` requests land within ``span`` seconds,
    then answers 202 to everything in that span, as DuckDuckGo does."""

    def __init__(self, clock: _Clock, limit: int = 14, span: float = 35.0) -> None:
        self.clock = clock
        self.limit = limit
        self.span = span
        self.sent = []
        self.refused = 0

    def __call__(self, method, url, **kwargs):
        now = self.clock.now
        self.sent.append(now)
        recent = [t for t in self.sent if t > now - self.span]
        response = MagicMock()
        response.headers = {"Content-Type": "text/html"}
        if len(recent) > self.limit:
            self.refused += 1
            response.status_code = 202
            response.text = "<html></html>"
        else:
            response.status_code = 200
            response.text = _page(kwargs["data"]["q"])
        return response


def _install_clock(monkeypatch, burst: int, window: float) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(web_client.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(browser_tools.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(web_client.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(
        web_client,
        "_SEARCH_GATE",
        _SearchGate(min_interval=1.5, sleep=clock.sleep, burst=burst, window=window),
    )
    return clock


class _Host(BrowserToolsMixin):
    def __init__(self, web, manager=None, active=()):
        self._web_client = web
        self._path_validator = None
        self._tools = {}
        if manager is not None:
            self._mcp_manager = manager
        self._active = list(active)

    def _active_mcp_servers(self, manager):
        return self._active if manager is not None else []


def _search_tool(web, **host_kwargs):
    tools = {}

    def capture(atomic=True):
        def decorator(func):
            tools[func.__name__] = func
            return func

        return decorator

    host = _Host(web, **host_kwargs)
    with patch("gaia.agents.base.tools.tool", capture):
        host.register_browser_tools()
    return host, tools["search_web"]


def _configured(monkeypatch, *ids, disabled=()):
    monkeypatch.setattr(
        "gaia.connectors.mcp_server.is_mcp_server_configured", lambda cid: cid in ids
    )
    monkeypatch.setattr(
        "gaia.connectors.mcp_server.is_mcp_server_enabled",
        lambda cid: cid not in disabled,
    )


@pytest.fixture
def no_tavily_client(monkeypatch):
    monkeypatch.setattr(
        "gaia.web.tavily.TavilyClient",
        lambda: pytest.fail("Tavily is not configured, so it must not be built"),
    )


def _unavailable():
    return SearchUnavailableError(
        "DuckDuckGo turned the search away (HTTP 202)", retry_after=8.0, status=202
    )


# ── a 25-search turn against a DuckDuckGo that refuses bursts ────────────────


class TestLongResearchTurn:
    QUERIES = [f"topic {i}" for i in range(25)]

    def _run_turn(self, monkeypatch, burst, window):
        _configured(monkeypatch)
        clock = _install_clock(monkeypatch, burst, window)
        web = WebClient()
        ddg = _FakeDDG(clock)
        try:
            with patch.object(web, "_request", side_effect=ddg):
                _, search_web = _search_tool(web)
                results = [search_web(q) for q in self.QUERIES]
        finally:
            web.close()
        return results, ddg, clock

    def test_production_pacing_keeps_every_search_of_the_turn(
        self, monkeypatch, no_tavily_client
    ):
        gate = web_client._SEARCH_GATE
        results, ddg, _ = self._run_turn(monkeypatch, gate.burst, gate.window)

        assert all(isinstance(r, str) and "1. Hit" in r for r in results)
        assert ddg.refused == 0
        assert len(ddg.sent) == 25

    def test_without_pacing_the_same_turn_loses_searches(
        self, monkeypatch, no_tavily_client
    ):
        # The control: only the burst limit differs from the test above.
        results, ddg, _ = self._run_turn(monkeypatch, burst=0, window=0.0)

        assert ddg.refused > 0
        lost = [r for r in results if isinstance(r, dict)]
        assert lost, "the fake DuckDuckGo should refuse an unpaced burst"
        # Never silent: every lost search says so and how long to wait.
        assert all("temporarily unavailable" in r["error"] for r in lost)

    def test_repeats_cost_no_requests(self, monkeypatch, no_tavily_client):
        _configured(monkeypatch)
        clock = _install_clock(monkeypatch, burst=10, window=60.0)
        web = WebClient()
        ddg = _FakeDDG(clock)
        queries = ["Ryzen AI NPU", "ryzen ai npu", "  Ryzen   AI\tNPU ", "RYZEN AI NPU"]
        try:
            with patch.object(web, "_request", side_effect=ddg):
                _, search_web = _search_tool(web)
                results = [search_web(q) for q in queries * 6]
        finally:
            web.close()

        assert len(ddg.sent) == 1
        assert "already ran this search" not in results[0]
        assert all("already ran this search" in r for r in results[1:])
        assert all("https://example.com/Ryzen-AI-NPU" in r for r in results)


# ── the session cache ────────────────────────────────────────────────────────


def _ddg_results(n=5, tag="r"):
    return [
        {"title": f"{tag}{i}", "url": f"https://example.com/{tag}{i}", "snippet": ""}
        for i in range(n)
    ]


class TestSearchCache:
    @pytest.fixture(autouse=True)
    def _none_configured(self, monkeypatch):
        _configured(monkeypatch)

    def test_different_queries_are_not_merged(self):
        web = MagicMock()
        web.search_duckduckgo.side_effect = [
            _ddg_results(tag="a"),
            _ddg_results(tag="b"),
        ]
        _, search_web = _search_tool(web)

        assert "a0" in search_web("amd npu")
        # Punctuation and word order change what a search finds.
        assert "b0" in search_web("amd, npu")
        assert web.search_duckduckgo.call_count == 2

    def test_a_smaller_ask_is_served_from_a_bigger_one(self):
        web = MagicMock()
        web.search_duckduckgo.return_value = _ddg_results(8)
        _, search_web = _search_tool(web)

        search_web("q", num_results=8)
        result = search_web("q", num_results=3)

        assert web.search_duckduckgo.call_count == 1
        assert "3. r2" in result and "r3" not in result

    def test_a_bigger_ask_searches_again(self):
        web = MagicMock()
        web.search_duckduckgo.return_value = _ddg_results(3)
        _, search_web = _search_tool(web)

        search_web("q", num_results=3)
        search_web("q", num_results=8)

        assert web.search_duckduckgo.call_count == 2

    def test_a_bigger_ask_after_an_exhausted_search_is_cached(self):
        web = MagicMock()
        web.search_duckduckgo.return_value = _ddg_results(2)
        _, search_web = _search_tool(web)

        search_web("q", num_results=5)
        search_web("q", num_results=10)

        # Only two results existed, so asking for more would find none.
        assert web.search_duckduckgo.call_count == 1

    def test_entries_expire(self, monkeypatch):
        clock = {"now": 0.0}
        monkeypatch.setattr(browser_tools.time, "monotonic", lambda: clock["now"])
        web = MagicMock()
        web.search_duckduckgo.return_value = _ddg_results()
        _, search_web = _search_tool(web)

        search_web("q")
        clock["now"] += SEARCH_CACHE_TTL_SECONDS + 1
        search_web("q")

        assert web.search_duckduckgo.call_count == 2

    def test_refusals_and_empty_answers_are_not_cached(self):
        web = MagicMock()
        web.search_duckduckgo.side_effect = [_unavailable(), [], _ddg_results()]
        _, search_web = _search_tool(web)

        assert search_web("q")["rate_limited"] is True
        assert search_web("q").startswith("No results found")
        assert "1. r0" in search_web("q")
        assert web.search_duckduckgo.call_count == 3

    def test_the_cache_is_per_session(self):
        web = MagicMock()
        web.search_duckduckgo.return_value = _ddg_results()
        _, first = _search_tool(web)
        _, second = _search_tool(web)

        first("q")
        second("q")

        assert web.search_duckduckgo.call_count == 2

    def test_the_cache_is_bounded(self, monkeypatch):
        monkeypatch.setattr(browser_tools, "SEARCH_CACHE_SIZE", 3)
        web = MagicMock()
        web.search_duckduckgo.return_value = _ddg_results()
        host, search_web = _search_tool(web)

        for i in range(10):
            search_web(f"q{i}")

        assert len(host._search_cache._entries) == 3
        search_web("q0")
        assert web.search_duckduckgo.call_count == 11


# ── proactive pacing ─────────────────────────────────────────────────────────


class TestBurstPacing:
    def test_the_burst_runs_back_to_back_then_spreads(self, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(web_client.time, "monotonic", lambda: clock.now)
        gate = _SearchGate(min_interval=0.0, sleep=clock.sleep, burst=3, window=10.0)

        starts = []
        for _ in range(7):
            gate.wait_turn()
            starts.append(clock.now - 1000.0)

        assert starts == [0.0, 0.0, 0.0, 10.0, 10.0, 10.0, 20.0]

    def test_no_window_ever_holds_more_than_the_burst(self, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(web_client.time, "monotonic", lambda: clock.now)
        gate = _SearchGate(min_interval=1.5, sleep=clock.sleep, burst=10, window=60.0)

        starts = []
        for _ in range(40):
            gate.wait_turn()
            starts.append(clock.now)

        for t in starts:
            assert sum(1 for s in starts if t <= s < t + 60.0) <= 10

    def test_parallel_callers_share_the_burst(self, monkeypatch):
        monkeypatch.setattr(web_client.time, "monotonic", lambda: 1000.0)
        slept = []
        gate = _SearchGate(min_interval=0.0, sleep=slept.append, burst=2, window=30.0)
        threads = [threading.Thread(target=gate.wait_turn) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Two go now, two wait one window, the fifth two windows.
        assert sorted(slept) == [30.0, 30.0, 60.0]

    def test_stop_ends_a_pacing_wait(self, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(web_client.time, "monotonic", lambda: clock.now)
        gate = _SearchGate(min_interval=0.0, sleep=clock.sleep, burst=1, window=60.0)
        gate.wait_turn()

        with pytest.raises(ToolCancelled):
            gate.wait_turn(should_stop=lambda: len(clock.slept) >= 2)
        assert sum(clock.slept) == pytest.approx(0.5)

    def test_the_process_gate_is_paced(self):
        gate = web_client._SEARCH_GATE
        assert 0 < gate.burst < 14
        assert gate.window >= 30.0


# ── vendor-neutral connector fallback ────────────────────────────────────────


def _brave_client(text=None, tools=None, result=None):
    client = MagicMock()
    client.list_tools.return_value = (
        tools
        if tools is not None
        else [
            MCPTool(
                name="brave_web_search",
                description="",
                input_schema={"properties": {"query": {}, "count": {}}},
            )
        ]
    )
    client.call_tool.return_value = (
        result
        if result is not None
        else {
            "content": [
                {
                    "type": "text",
                    "text": text
                    or (
                        "Title: Brave One\nDescription: first\nURL: https://b.example/1"
                        "\n\nTitle: Brave Two\nDescription: second\n"
                        "URL: https://b.example/2"
                    ),
                }
            ]
        }
    )
    return client


def _manager(clients, failures=None):
    manager = MagicMock()
    manager.get_client.side_effect = clients.get
    manager.failure.side_effect = (failures or {}).get
    return manager


class TestConnectorFallback:
    def test_a_brave_only_setup_answers_when_ddg_refuses(
        self, monkeypatch, no_tavily_client
    ):
        _configured(monkeypatch, BRAVE)
        brave = _brave_client()
        web = MagicMock()
        web.search_duckduckgo.side_effect = _unavailable()
        _, search_web = _search_tool(
            web, manager=_manager({BRAVE: brave}), active=[BRAVE]
        )

        result = search_web("ryzen ai", num_results=3)

        brave.call_tool.assert_called_once_with(
            "brave_web_search", {"query": "ryzen ai", "count": 3}
        )
        assert "configured Brave Search connector" in result
        assert "1. Brave One" in result and "https://b.example/2" in result

    def test_brave_answers_are_cached_too(self, monkeypatch, no_tavily_client):
        _configured(monkeypatch, BRAVE)
        brave = _brave_client()
        web = MagicMock()
        web.search_duckduckgo.side_effect = _unavailable()
        _, search_web = _search_tool(
            web, manager=_manager({BRAVE: brave}), active=[BRAVE]
        )

        search_web("q")
        again = search_web("Q")

        assert brave.call_tool.call_count == 1
        assert web.search_duckduckgo.call_count == 1
        assert "Brave Search connector" in again

    def test_ddg_stays_first_when_a_connector_is_configured(self, monkeypatch):
        _configured(monkeypatch, BRAVE, TAVILY)
        brave = _brave_client()
        web = MagicMock()
        web.search_duckduckgo.return_value = _ddg_results()
        _, search_web = _search_tool(
            web, manager=_manager({BRAVE: brave}), active=[BRAVE]
        )

        assert "1. r0" in search_web("q")
        brave.call_tool.assert_not_called()

    def test_tavily_comes_first_and_keeps_its_own_client(self, monkeypatch):
        _configured(monkeypatch, BRAVE, TAVILY)
        tavily = MagicMock(configured=True)
        tavily.search.return_value = {
            "results": [{"title": "Tav", "url": "https://t.example", "content": ""}]
        }
        monkeypatch.setattr("gaia.web.tavily.TavilyClient", lambda: tavily)
        brave = _brave_client()
        web = MagicMock()
        web.search_duckduckgo.side_effect = _unavailable()
        _, search_web = _search_tool(
            web, manager=_manager({BRAVE: brave}), active=[BRAVE]
        )

        result = search_web("q")

        assert "Tavily connector" in result and "1. Tav" in result
        brave.call_tool.assert_not_called()

    def test_a_failing_connector_falls_through_to_the_next(self, monkeypatch):
        _configured(monkeypatch, BRAVE, TAVILY)
        tavily = MagicMock(configured=True)
        tavily.search.side_effect = RuntimeError("credit cap reached")
        monkeypatch.setattr("gaia.web.tavily.TavilyClient", lambda: tavily)
        web = MagicMock()
        web.search_duckduckgo.side_effect = _unavailable()
        _, search_web = _search_tool(
            web, manager=_manager({BRAVE: _brave_client()}), active=[BRAVE]
        )

        assert "1. Brave One" in search_web("q")

    def test_a_brave_not_running_for_this_agent_is_reported(
        self, monkeypatch, no_tavily_client
    ):
        _configured(monkeypatch, BRAVE)
        web = MagicMock()
        web.search_duckduckgo.side_effect = _unavailable()
        brave = _brave_client()
        _, search_web = _search_tool(
            web,
            manager=_manager({BRAVE: brave}, {BRAVE: "npx not found"}),
            active=[],
        )

        results = [search_web(f"q{i}") for i in range(SEARCH_UNAVAILABLE_STREAK_LIMIT)]

        brave.call_tool.assert_not_called()
        assert "Brave Search connector also failed" in results[0]["error"]
        assert "not running for this agent (npx not found)" in results[0]["error"]
        assert "Settings > Connectors" in results[0]["error"]
        # It is set up, so the user is not told to set one up.
        assert "No backup search provider" not in results[-1]["hint"]

    def test_an_agent_without_mcp_reports_the_connector(
        self, monkeypatch, no_tavily_client
    ):
        _configured(monkeypatch, BRAVE)
        web = MagicMock()
        web.search_duckduckgo.side_effect = _unavailable()
        _, search_web = _search_tool(web)

        result = search_web("q")

        assert result["rate_limited"] is True
        assert "not running for this agent" in result["error"]

    def test_a_turned_off_connector_is_not_used(self, monkeypatch, no_tavily_client):
        _configured(monkeypatch, BRAVE, disabled=(BRAVE,))
        brave = _brave_client()
        web = MagicMock()
        web.search_duckduckgo.side_effect = _unavailable()
        _, search_web = _search_tool(
            web, manager=_manager({BRAVE: brave}), active=[BRAVE]
        )

        results = [search_web(f"q{i}") for i in range(SEARCH_UNAVAILABLE_STREAK_LIMIT)]

        brave.call_tool.assert_not_called()
        assert "No backup search provider" in results[-1]["hint"]

    @pytest.mark.parametrize(
        "result, message",
        [
            ({"error": "401 Unauthorized"}, "401 Unauthorized"),
            (
                {"isError": True, "content": [{"type": "text", "text": "quota"}]},
                "quota",
            ),
            (
                {"content": [{"type": "text", "text": "<html>gateway</html>"}]},
                "could not read results",
            ),
        ],
    )
    def test_a_connector_error_is_reported_not_hidden(
        self, monkeypatch, no_tavily_client, result, message
    ):
        _configured(monkeypatch, BRAVE)
        web = MagicMock()
        web.search_duckduckgo.side_effect = _unavailable()
        _, search_web = _search_tool(
            web, manager=_manager({BRAVE: _brave_client(result=result)}), active=[BRAVE]
        )

        answer = search_web("q")

        assert answer["rate_limited"] is True
        assert message in answer["error"]

    def test_a_server_without_the_search_tool_is_reported(
        self, monkeypatch, no_tavily_client
    ):
        _configured(monkeypatch, BRAVE)
        web = MagicMock()
        web.search_duckduckgo.side_effect = _unavailable()
        brave = _brave_client(tools=[MCPTool("brave_local_search", "", {})])
        _, search_web = _search_tool(
            web, manager=_manager({BRAVE: brave}), active=[BRAVE]
        )

        assert "no brave_web_search tool" in search_web("q")["error"]
        brave.call_tool.assert_not_called()

    def test_a_tool_without_a_count_argument_gets_only_the_query(
        self, monkeypatch, no_tavily_client
    ):
        _configured(monkeypatch, BRAVE)
        brave = _brave_client(
            tools=[MCPTool("brave_web_search", "", {"properties": {"query": {}}})]
        )
        web = MagicMock()
        web.search_duckduckgo.side_effect = _unavailable()
        _, search_web = _search_tool(
            web, manager=_manager({BRAVE: brave}), active=[BRAVE]
        )

        result = search_web("q", num_results=1)

        brave.call_tool.assert_called_once_with("brave_web_search", {"query": "q"})
        assert "1. Brave One" in result and "Brave Two" not in result


class TestNoConnector:
    def test_the_wait_is_bounded_and_names_the_connectors_page(
        self, monkeypatch, no_tavily_client
    ):
        _configured(monkeypatch)
        web = MagicMock()
        web.search_duckduckgo.side_effect = _unavailable()
        _, search_web = _search_tool(web)

        results = [search_web(f"q{i}") for i in range(SEARCH_UNAVAILABLE_STREAK_LIMIT)]

        assert all(r["wait_time_seconds"] == 8.0 for r in results[:-1])
        hint = results[-1]["hint"]
        assert "Settings > Connectors" in hint
        assert "Tavily" in hint and "Brave Search" in hint
        assert "gaia connectors configure" not in hint
        assert "Do not invent search results" in hint


# ── the capability on ConnectorSpec ──────────────────────────────────────────


class TestSearchCapability:
    def test_catalog_search_connectors(self):
        ids = [spec.id for spec in browser_tools._search_connectors()]
        assert ids == [TAVILY, BRAVE]

    def test_every_search_connector_names_its_tool(self):
        for spec in browser_tools._search_connectors():
            assert spec.type == "mcp_server" and spec.web_search_tool

    def test_providing_search_without_a_tool_is_refused(self):
        with pytest.raises(ValueError, match="web_search_tool"):
            ConnectorSpec(
                id="mcp-x",
                display_name="X",
                icon="x",
                category="dev-tools",
                tier=2,
                type="mcp_server",
                description="d",
                provides=[WEB_SEARCH],
            )

    def test_provides_is_a_tuple(self):
        spec = ConnectorSpec(
            id="mcp-x",
            display_name="X",
            icon="x",
            category="dev-tools",
            tier=2,
            type="mcp_server",
            description="d",
            provides=[WEB_SEARCH],
            web_search_tool="search",
        )
        assert spec.provides == (WEB_SEARCH,)


class TestParseSearchText:
    def test_tavily_mcp_text(self):
        text = (
            "Answer: something\nDetailed Results:\n\nTitle: T\nURL: https://t\n"
            "Content: c\nRaw Content: page body\n\nTitle: U\nURL: https://u\n"
            "Content: c2"
        )
        assert _parse_search_text(text) == [
            {"title": "T", "url": "https://t", "snippet": "c"},
            {"title": "U", "url": "https://u", "snippet": "c2"},
        ]

    def test_json_results(self):
        text = (
            '{"results": [{"title": "j", "url": "https://j", "description": "d"},'
            ' {"title": "no url"}, "junk"]}'
        )
        assert _parse_search_text(text) == [
            {"title": "j", "url": "https://j", "snippet": "d"}
        ]

    def test_a_wrapped_snippet_continues(self):
        text = "Title: T\nURL: https://t\nDescription: first line\nopens 10:30 daily"
        assert _parse_search_text(text)[0]["snippet"] == "first line opens 10:30 daily"

    def test_a_long_snippet_is_cut(self):
        text = "Title: T\nURL: https://t\nDescription: " + "x" * 5000
        assert len(_parse_search_text(text)[0]["snippet"]) == 500

    def test_results_without_urls_are_dropped(self):
        assert _parse_search_text("Title: A\nDescription: no link") == []
        assert _parse_search_text("") == []
