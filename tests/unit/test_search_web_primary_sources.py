# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""search_web lists the subject's own pages first and steers release questions to them."""

from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.tools.browser_tools import (
    _RELEASE_NO_PRIMARY_NOTE,
    _RELEASE_PRIMARY_NOTE,
    BrowserToolsMixin,
    _is_primary_source,
    _is_release_query,
    _project_words,
    _rank_by_source,
)

RELEASE_QUERY = "What is the latest released version of Lemonade Server"

NEWS = {
    "title": "AMD Lemonade 11.5 Released",
    "url": "https://www.phoronix.com/news/AMD-Lemonade-11.5",
    "snippet": "Lemonade 11.5 is out with ...",
}
RELEASES = {
    "title": "Releases · lemonade-sdk/lemonade",
    "url": "https://github.com/lemonade-sdk/lemonade/releases",
    "snippet": "2026.44.1 Latest",
}
AGGREGATOR = {
    "title": "Lemonade Server alternatives",
    "url": "https://alternativeto.net/software/lemonade-server/",
    "snippet": "",
}


@pytest.fixture(autouse=True)
def _no_tavily(monkeypatch):
    monkeypatch.setattr(
        "gaia.connectors.mcp_server.is_mcp_server_configured", lambda _id: False
    )


@pytest.fixture
def search_web():
    class MockAgent(BrowserToolsMixin):
        def __init__(self):
            self._web_client = MagicMock()
            self._path_validator = None
            self._tools = {}

    tools = {}

    def mock_tool(atomic=True):
        def decorator(func):
            tools[func.__name__] = func
            return func

        return decorator

    with patch("gaia.agents.base.tools.tool", mock_tool):
        agent = MockAgent()
        agent.register_browser_tools()

    def run(query, results):
        agent._web_client.search_duckduckgo.return_value = results
        return tools["search_web"](query)

    return run


class TestProjectWords:
    def test_drops_question_words_and_short_tokens(self):
        assert _project_words(RELEASE_QUERY) == {"lemonade"}

    def test_drops_bare_numbers(self):
        assert _project_words("python 3.13 2026 release") == {"python"}


class TestIsReleaseQuery:
    @pytest.mark.parametrize(
        "query",
        [RELEASE_QUERY, "llama.cpp releases", "ffmpeg changelog", "node versions"],
    )
    def test_release_questions(self, query):
        assert _is_release_query(query)

    @pytest.mark.parametrize("query", ["best pizza in austin", "latest AMD news"])
    def test_other_questions(self, query):
        assert not _is_release_query(query)


class TestIsPrimarySource:
    words = frozenset({"lemonade"})

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/lemonade-sdk/lemonade/releases",
            "https://lemonade-server.ai/docs/",
            "https://www.lemonade-server.ai/",
            "https://pypi.org/project/lemonade-sdk/",
            "https://lemonade.readthedocs.io/en/latest/",
        ],
    )
    def test_the_projects_own_pages(self, url):
        assert _is_primary_source(url, self.words)

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.phoronix.com/news/AMD-Lemonade-11.5",
            "https://alternativeto.net/software/lemonade-server/",
            "https://github.com/someone/unrelated-tool",
            "https://pypi.org/project/other/",
            "not a url",
            "",
        ],
    )
    def test_news_aggregators_and_other_projects(self, url):
        assert not _is_primary_source(url, self.words)

    def test_whole_label_parts_only(self):
        words = frozenset({"python"})
        assert _is_primary_source("https://docs.python.org/3/", words)
        assert not _is_primary_source("https://realpython.com/", words)

    def test_no_project_words_means_nothing_is_primary(self):
        assert not _is_primary_source(RELEASES["url"], frozenset())


class TestRankBySource:
    def test_primary_first_and_otherwise_stable(self):
        ranked = _rank_by_source([NEWS, AGGREGATOR, RELEASES], RELEASE_QUERY)
        assert [r for r, _ in ranked] == [RELEASES, NEWS, AGGREGATOR]
        assert [p for _, p in ranked] == [True, False, False]

    def test_missing_url_is_secondary(self):
        assert _rank_by_source([{"title": "x"}], RELEASE_QUERY) == [
            ({"title": "x"}, False)
        ]


class TestSearchWebOutput:
    def test_release_page_listed_first_and_marked(self, search_web):
        out = search_web(RELEASE_QUERY, [NEWS, RELEASES])
        assert out.index("1. Releases · lemonade-sdk/lemonade [primary source]") < (
            out.index("2. AMD Lemonade 11.5 Released")
        )
        assert _RELEASE_PRIMARY_NOTE in out
        assert _RELEASE_NO_PRIMARY_NOTE not in out

    def test_all_secondary_release_results_say_so(self, search_web):
        out = search_web(RELEASE_QUERY, [NEWS, AGGREGATOR])
        assert "[primary source]" not in out
        assert _RELEASE_NO_PRIMARY_NOTE in out

    def test_other_questions_get_no_release_note(self, search_web):
        out = search_web("best lemonade recipe", [NEWS, AGGREGATOR])
        assert _RELEASE_PRIMARY_NOTE not in out
        assert _RELEASE_NO_PRIMARY_NOTE not in out
        assert out.endswith(
            "Use fetch_page(url) to read the full content of any result."
        )
