# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for web_search and read_webpage tools (M3: Service Integration).

Tests validate:
- web_search: Perplexity-backed web search with graceful error handling
- read_webpage: WebClient-backed URL content extraction (text, links, full)
- WebSearchMixin: Tool registration on any agent
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from gaia.agents.base.tools import _TOOL_REGISTRY

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_tool_registry():
    """Clear the global tool registry before and after each test."""
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture
def sample_html():
    """Sample HTML page for testing read_webpage."""
    return """<!DOCTYPE html>
<html>
<head><title>Test Page</title></head>
<body>
  <nav><a href="/home">Home</a></nav>
  <h1>Main Heading</h1>
  <p>This is the main content of the test page. It contains important information.</p>
  <p>Second paragraph with more details about the topic.</p>
  <a href="https://example.com/page1">Link One</a>
  <a href="https://example.com/page2">Link Two</a>
  <a href="/relative-link">Relative Link</a>
  <footer>Footer content</footer>
</body>
</html>"""


@pytest.fixture
def large_html():
    """Very large HTML page for truncation testing."""
    paragraphs = "\n".join(
        [
            f"<p>Paragraph {i} with some content about topic {i}.</p>"
            for i in range(2000)
        ]
    )
    return f"""<!DOCTYPE html>
<html>
<head><title>Large Page</title></head>
<body>
  <h1>Large Document</h1>
  {paragraphs}
</body>
</html>"""


@pytest.fixture
def mock_web_client():
    """Create a mock WebClient for read_webpage tests."""
    client = MagicMock()
    return client


@pytest.fixture
def register_tools(mock_web_client):
    """Register WebSearchMixin tools and return helper to access them."""
    from gaia.agents.tools.web_search import WebSearchMixin

    class FakeAgent(WebSearchMixin):
        pass

    agent = FakeAgent()
    agent._web_client = mock_web_client
    agent.register_web_search_tools()

    def get_tool(name):
        return _TOOL_REGISTRY[name]["function"]

    return get_tool


# ===========================================================================
# web_search tests
# ===========================================================================


class TestWebSearchReturnsResults:
    """test_web_search_returns_results: Mock Perplexity -> returns structured results with sources."""

    @patch("gaia.agents.tools.web_search._call_perplexity_api")
    def test_returns_structured_results(self, mock_perplexity, register_tools):
        mock_perplexity.return_value = {
            "success": True,
            "answer": "AI trends in 2026 include local inference and AMD NPU optimization.",
            "sources": ["https://example.com/ai-trends"],
        }

        web_search = register_tools("web_search")
        result = web_search("AI trends 2026")

        assert result["success"] is True
        assert "AI trends" in result["answer"]
        assert isinstance(result.get("sources", []), list)

    @patch("gaia.agents.tools.web_search._call_perplexity_api")
    def test_returns_answer_text(self, mock_perplexity, register_tools):
        mock_perplexity.return_value = {
            "success": True,
            "answer": "Python 3.12 introduced several improvements.",
            "sources": [],
        }

        web_search = register_tools("web_search")
        result = web_search("Python 3.12 features")

        assert result["success"] is True
        assert len(result["answer"]) > 0


class TestWebSearchNoApiKey:
    """test_web_search_no_api_key: Graceful error when PERPLEXITY_API_KEY not set."""

    @patch.dict(os.environ, {}, clear=True)
    @patch("gaia.agents.tools.web_search._call_perplexity_api")
    def test_no_api_key_returns_error(self, mock_perplexity, register_tools):
        mock_perplexity.return_value = {
            "success": False,
            "error": "PERPLEXITY_API_KEY not set",
            "answer": "",
            "sources": [],
        }

        web_search = register_tools("web_search")
        result = web_search("test query")

        assert result["success"] is False
        assert "error" in result
        assert (
            "PERPLEXITY_API_KEY" in result["error"]
            or "api key" in result["error"].lower()
        )

    @patch.dict(os.environ, {}, clear=True)
    @patch("gaia.agents.tools.web_search._call_perplexity_api")
    def test_no_api_key_does_not_crash(self, mock_perplexity, register_tools):
        """Should return a dict, never raise an exception."""
        mock_perplexity.return_value = {
            "success": False,
            "error": "PERPLEXITY_API_KEY not set",
            "answer": "",
            "sources": [],
        }

        web_search = register_tools("web_search")
        result = web_search("test query")

        assert isinstance(result, dict)


class TestWebSearchServiceUnavailable:
    """test_web_search_service_unavailable: Graceful fallback when Perplexity MCP isn't running."""

    @patch("gaia.agents.tools.web_search._call_perplexity_api")
    def test_service_unavailable_returns_error(self, mock_perplexity, register_tools):
        mock_perplexity.return_value = {
            "success": False,
            "error": "Perplexity service unavailable",
            "answer": "",
            "sources": [],
        }

        web_search = register_tools("web_search")
        result = web_search("test query")

        assert result["success"] is False
        assert "error" in result

    @patch("gaia.agents.tools.web_search._call_perplexity_api")
    def test_service_exception_handled(self, mock_perplexity, register_tools):
        """Even if _call_perplexity_api raises, web_search should not crash."""
        mock_perplexity.side_effect = ConnectionError("Connection refused")

        web_search = register_tools("web_search")
        result = web_search("test query")

        assert result["success"] is False
        assert "error" in result


# ===========================================================================
# read_webpage tests
# ===========================================================================


class TestReadWebpageExtractsText:
    """test_read_webpage_extracts_text: Mock HTTP response with HTML -> clean text."""

    def test_extracts_text_content(self, register_tools, mock_web_client, sample_html):
        # Mock WebClient.get() returning an HTML response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()
        mock_web_client.get.return_value = mock_response

        # Mock parse_html and extract_text
        mock_soup = MagicMock()
        mock_title_tag = MagicMock()
        mock_title_tag.get_text.return_value = "Test Page"
        mock_soup.find.return_value = mock_title_tag
        mock_web_client.parse_html.return_value = mock_soup
        mock_web_client.extract_text.return_value = (
            "Main Heading\nThis is the main content of the test page."
        )

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://example.com/test", extract="text")

        assert result["success"] is True
        assert "content" in result
        assert len(result["content"]) > 0
        mock_web_client.get.assert_called_once()

    def test_strips_nav_and_footer(self, register_tools, mock_web_client, sample_html):
        """Text extraction should exclude nav/footer (handled by WebClient.extract_text)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()
        mock_web_client.get.return_value = mock_response

        mock_soup = MagicMock()
        mock_title_tag = MagicMock()
        mock_title_tag.get_text.return_value = "Test Page"
        mock_soup.find.return_value = mock_title_tag
        mock_web_client.parse_html.return_value = mock_soup
        mock_web_client.extract_text.return_value = "Main Heading\nMain content only."

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://example.com/test", extract="text")

        assert result["success"] is True
        assert "Footer" not in result.get("content", "")


class TestReadWebpageExtractsLinks:
    """test_read_webpage_extracts_links: Mock HTTP -> returns list of links."""

    def test_returns_links_list(self, register_tools, mock_web_client, sample_html):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()
        mock_web_client.get.return_value = mock_response

        mock_soup = MagicMock()
        mock_title_tag = MagicMock()
        mock_title_tag.get_text.return_value = "Test Page"
        mock_soup.find.return_value = mock_title_tag
        mock_web_client.parse_html.return_value = mock_soup
        mock_web_client.extract_links.return_value = [
            {"text": "Link One", "url": "https://example.com/page1"},
            {"text": "Link Two", "url": "https://example.com/page2"},
            {"text": "Relative Link", "url": "https://example.com/relative-link"},
        ]

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://example.com/test", extract="links")

        assert result["success"] is True
        assert "links" in result
        assert isinstance(result["links"], list)
        assert len(result["links"]) >= 2

    def test_links_have_text_and_url(
        self, register_tools, mock_web_client, sample_html
    ):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()
        mock_web_client.get.return_value = mock_response

        mock_soup = MagicMock()
        mock_title_tag = MagicMock()
        mock_title_tag.get_text.return_value = "Test Page"
        mock_soup.find.return_value = mock_title_tag
        mock_web_client.parse_html.return_value = mock_soup
        mock_web_client.extract_links.return_value = [
            {"text": "Link One", "url": "https://example.com/page1"},
        ]

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://example.com/test", extract="links")

        assert result["success"] is True
        for link in result["links"]:
            assert "text" in link
            assert "url" in link


class TestReadWebpageHandlesTimeout:
    """test_read_webpage_handles_timeout: URL that times out returns clear error."""

    def test_timeout_returns_error(self, register_tools, mock_web_client):
        import requests

        mock_web_client.get.side_effect = requests.exceptions.Timeout(
            "Connection timed out"
        )

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://slow-site.example.com/page")

        assert result["success"] is False
        assert "error" in result
        assert (
            "timeout" in result["error"].lower()
            or "timed out" in result["error"].lower()
        )

    def test_timeout_does_not_crash(self, register_tools, mock_web_client):
        import requests

        mock_web_client.get.side_effect = requests.exceptions.ConnectTimeout(
            "Connect timed out"
        )

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://slow-site.example.com/page")

        assert isinstance(result, dict)
        assert result["success"] is False


class TestReadWebpageHandlesInvalidUrl:
    """test_read_webpage_handles_invalid_url: Bad URL returns clear error."""

    def test_invalid_url_returns_error(self, register_tools, mock_web_client):
        mock_web_client.get.side_effect = ValueError(
            "Blocked URL scheme: ftp. Only http/https allowed."
        )

        read_webpage = register_tools("read_webpage")
        result = read_webpage("ftp://invalid.example.com/file")

        assert result["success"] is False
        assert "error" in result

    def test_empty_url_returns_error(self, register_tools, mock_web_client):
        mock_web_client.get.side_effect = ValueError("Invalid URL: no hostname in ")

        read_webpage = register_tools("read_webpage")
        result = read_webpage("")

        assert result["success"] is False
        assert "error" in result

    def test_malformed_url_returns_error(self, register_tools, mock_web_client):
        mock_web_client.get.side_effect = ValueError(
            "Cannot resolve hostname: not-a-real-host"
        )

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://not-a-real-host/path")

        assert result["success"] is False
        assert "error" in result


class TestReadWebpageTruncatesLargePages:
    """test_read_webpage_truncates_large_pages: Very large HTML truncated to reasonable size."""

    def test_large_page_is_truncated(self, register_tools, mock_web_client, large_html):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = large_html
        mock_response.raise_for_status = MagicMock()
        mock_web_client.get.return_value = mock_response

        mock_soup = MagicMock()
        mock_title_tag = MagicMock()
        mock_title_tag.get_text.return_value = "Large Page"
        mock_soup.find.return_value = mock_title_tag
        mock_web_client.parse_html.return_value = mock_soup

        # Simulate extract_text returning a very long string
        huge_text = "Content " * 50000  # ~400k chars
        mock_web_client.extract_text.return_value = huge_text

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://example.com/large-page", extract="text")

        assert result["success"] is True
        # Content should be truncated to a reasonable size (max ~50k chars for LLM context)
        assert (
            len(result["content"]) <= 50000 + 100
        )  # small buffer for truncation message
        assert result.get("truncated", False) is True

    def test_normal_page_not_truncated(
        self, register_tools, mock_web_client, sample_html
    ):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()
        mock_web_client.get.return_value = mock_response

        mock_soup = MagicMock()
        mock_title_tag = MagicMock()
        mock_title_tag.get_text.return_value = "Test Page"
        mock_soup.find.return_value = mock_title_tag
        mock_web_client.parse_html.return_value = mock_soup
        mock_web_client.extract_text.return_value = "Short content."

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://example.com/test", extract="text")

        assert result["success"] is True
        assert result.get("truncated", False) is False


class TestReadWebpageNonHtml:
    """test_read_webpage_non_html: Non-HTML content handled gracefully."""

    def test_json_content_returned_as_text(self, register_tools, mock_web_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.text = '{"key": "value", "items": [1, 2, 3]}'
        mock_response.raise_for_status = MagicMock()
        mock_web_client.get.return_value = mock_response

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://api.example.com/data.json")

        assert result["success"] is True
        assert "content" in result
        assert "key" in result["content"]

    def test_binary_content_returns_info(self, register_tools, mock_web_client):
        """Binary content (PDF, image) should return info, not crash."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "Content-Type": "application/pdf",
            "Content-Length": "1048576",
        }
        mock_response.text = "%PDF-1.4 binary content..."
        mock_response.raise_for_status = MagicMock()
        mock_web_client.get.return_value = mock_response

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://example.com/document.pdf")

        assert result["success"] is True
        assert "content" in result
        # Should mention it's binary/non-HTML
        assert (
            "binary" in result["content"].lower() or "pdf" in result["content"].lower()
        )

    def test_plain_text_returned(self, register_tools, mock_web_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_response.text = "Plain text content here.\nSecond line."
        mock_response.raise_for_status = MagicMock()
        mock_web_client.get.return_value = mock_response

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://example.com/file.txt")

        assert result["success"] is True
        assert "Plain text content" in result["content"]


class TestWebSearchMixinRegistersTools:
    """test_web_search_mixin_registers_tools: Agent with mixin has both tools in registry."""

    def test_both_tools_registered(self):
        from gaia.agents.tools.web_search import WebSearchMixin

        class FakeAgent(WebSearchMixin):
            pass

        agent = FakeAgent()
        agent._web_client = MagicMock()
        agent.register_web_search_tools()

        assert "web_search" in _TOOL_REGISTRY
        assert "read_webpage" in _TOOL_REGISTRY

    def test_tools_are_callable(self):
        from gaia.agents.tools.web_search import WebSearchMixin

        class FakeAgent(WebSearchMixin):
            pass

        agent = FakeAgent()
        agent._web_client = MagicMock()
        agent.register_web_search_tools()

        assert callable(_TOOL_REGISTRY["web_search"]["function"])
        assert callable(_TOOL_REGISTRY["read_webpage"]["function"])

    def test_tools_have_descriptions(self):
        from gaia.agents.tools.web_search import WebSearchMixin

        class FakeAgent(WebSearchMixin):
            pass

        agent = FakeAgent()
        agent._web_client = MagicMock()
        agent.register_web_search_tools()

        assert len(_TOOL_REGISTRY["web_search"]["description"]) > 0
        assert len(_TOOL_REGISTRY["read_webpage"]["description"]) > 0

    def test_tools_marked_atomic(self):
        from gaia.agents.tools.web_search import WebSearchMixin

        class FakeAgent(WebSearchMixin):
            pass

        agent = FakeAgent()
        agent._web_client = MagicMock()
        agent.register_web_search_tools()

        assert _TOOL_REGISTRY["web_search"]["atomic"] is True
        assert _TOOL_REGISTRY["read_webpage"]["atomic"] is True

    def test_web_search_has_query_param(self):
        from gaia.agents.tools.web_search import WebSearchMixin

        class FakeAgent(WebSearchMixin):
            pass

        agent = FakeAgent()
        agent._web_client = MagicMock()
        agent.register_web_search_tools()

        params = _TOOL_REGISTRY["web_search"]["parameters"]
        assert "query" in params
        assert params["query"]["required"] is True

    def test_read_webpage_has_url_and_extract_params(self):
        from gaia.agents.tools.web_search import WebSearchMixin

        class FakeAgent(WebSearchMixin):
            pass

        agent = FakeAgent()
        agent._web_client = MagicMock()
        agent.register_web_search_tools()

        params = _TOOL_REGISTRY["read_webpage"]["parameters"]
        assert "url" in params
        assert params["url"]["required"] is True
        assert "extract" in params
        assert params["extract"]["required"] is False


# ===========================================================================
# read_webpage "full" extract mode
# ===========================================================================


class TestReadWebpageFullMode:
    """Test 'full' extract mode returns complete HTML text."""

    def test_full_mode_returns_all_content(
        self, register_tools, mock_web_client, sample_html
    ):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()
        mock_web_client.get.return_value = mock_response

        mock_soup = MagicMock()
        mock_title_tag = MagicMock()
        mock_title_tag.get_text.return_value = "Test Page"
        mock_soup.find.return_value = mock_title_tag
        mock_soup.get_text.return_value = (
            "Home Main Heading This is the main content Footer content"
        )
        mock_web_client.parse_html.return_value = mock_soup

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://example.com/test", extract="full")

        assert result["success"] is True
        assert "content" in result


# ===========================================================================
# _call_perplexity_api direct tests
# ===========================================================================


class TestCallPerplexityApi:
    """Direct tests of the Perplexity API calling function."""

    def test_direct_api_success(self):
        """Test direct Perplexity HTTP API call."""
        from gaia.agents.tools.web_search import _call_perplexity_api

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "Here is information about AI trends.",
                    }
                }
            ],
            "citations": ["https://example.com/source1"],
        }

        with patch(
            "gaia.agents.tools.web_search.requests.post", return_value=mock_response
        ):
            with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test-key-123"}):
                result = _call_perplexity_api("AI trends 2026")

        assert result["success"] is True
        assert "AI trends" in result["answer"]

    def test_direct_api_no_key(self):
        """Test direct API call without API key."""
        from gaia.agents.tools.web_search import _call_perplexity_api

        with patch.dict(os.environ, {}, clear=True):
            # Make sure PERPLEXITY_API_KEY is not set
            os.environ.pop("PERPLEXITY_API_KEY", None)
            result = _call_perplexity_api("test query")

        assert result["success"] is False
        assert "PERPLEXITY_API_KEY" in result.get("error", "")

    def test_direct_api_http_error(self):
        """Test direct API call with HTTP error."""
        from gaia.agents.tools.web_search import _call_perplexity_api

        with patch("gaia.agents.tools.web_search.requests.post") as mock_post:
            mock_post.side_effect = Exception("Connection refused")
            with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test-key-123"}):
                result = _call_perplexity_api("test query")

        assert result["success"] is False
        assert "error" in result

    def test_direct_api_malformed_json(self):
        """Test direct API call when Perplexity returns malformed JSON."""
        import json as json_mod

        from gaia.agents.tools.web_search import _call_perplexity_api

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = json_mod.JSONDecodeError(
            "Expecting value", "doc", 0
        )

        with patch(
            "gaia.agents.tools.web_search.requests.post", return_value=mock_response
        ):
            with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test-key-123"}):
                result = _call_perplexity_api("test query")

        assert result["success"] is False
        assert "malformed" in result["error"].lower()

    def test_direct_api_rate_limited(self):
        """Test direct API call with 429 rate limit."""
        from gaia.agents.tools.web_search import _call_perplexity_api

        mock_response = MagicMock()
        mock_response.status_code = 429

        with patch(
            "gaia.agents.tools.web_search.requests.post", return_value=mock_response
        ):
            with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test-key-123"}):
                result = _call_perplexity_api("test query")

        assert result["success"] is False
        assert "rate limit" in result["error"].lower()


class TestReadWebpageHttpError:
    """Test read_webpage handling of HTTP status errors (404, 500, etc.)."""

    def test_http_404_returns_error(self, register_tools, mock_web_client):
        """404 Not Found should return a clear error."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        http_error = requests.exceptions.HTTPError(
            "404 Client Error: Not Found", response=mock_response
        )
        mock_response.raise_for_status.side_effect = http_error
        mock_web_client.get.return_value = mock_response

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://example.com/missing-page")

        assert result["success"] is False
        assert "error" in result
        assert "404" in result["error"]

    def test_http_500_returns_error(self, register_tools, mock_web_client):
        """500 Internal Server Error should return a clear error."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        http_error = requests.exceptions.HTTPError(
            "500 Server Error: Internal Server Error", response=mock_response
        )
        mock_response.raise_for_status.side_effect = http_error
        mock_web_client.get.return_value = mock_response

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://example.com/broken-page")

        assert result["success"] is False
        assert "error" in result
        assert "500" in result["error"]


class TestDoubleTruncationBug:
    """Regression: extract_text already truncates and appends a suffix.

    If _truncate_content runs on top of that, it double-truncates
    producing two '...' markers.  The code should only produce one.
    """

    def test_no_double_truncation_suffix(self, register_tools, mock_web_client):
        """Simulate extract_text returning text right at the boundary with its own suffix."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = "<html><body>big page</body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_web_client.get.return_value = mock_response

        mock_soup = MagicMock()
        mock_title_tag = MagicMock()
        mock_title_tag.get_text.return_value = "Big Page"
        mock_soup.find.return_value = mock_title_tag
        mock_web_client.parse_html.return_value = mock_soup

        # Simulate what real WebClient.extract_text does when it truncates:
        # returns max_length chars + "\n\n... (truncated)" suffix
        base_text = "x" * 50000
        text_with_suffix = base_text + "\n\n... (truncated)"
        mock_web_client.extract_text.return_value = text_with_suffix

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://example.com/large", extract="text")

        assert result["success"] is True
        # Must NOT contain two truncation markers
        assert result["content"].count("...") <= 1, (
            f"Double truncation detected: content has multiple '...' markers. "
            f"Ends with: ...{result['content'][-80:]}"
        )


class TestConsistentReturnShape:
    """All read_webpage success results should have both 'content' and 'links' keys."""

    def test_text_mode_has_links_key(
        self, register_tools, mock_web_client, sample_html
    ):
        """Text mode result should include a 'links' key (empty list)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()
        mock_web_client.get.return_value = mock_response

        mock_soup = MagicMock()
        mock_title_tag = MagicMock()
        mock_title_tag.get_text.return_value = "Test Page"
        mock_soup.find.return_value = mock_title_tag
        mock_web_client.parse_html.return_value = mock_soup
        mock_web_client.extract_text.return_value = "Some content."

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://example.com/test", extract="text")

        assert result["success"] is True
        assert "content" in result
        assert "links" in result
        assert isinstance(result["links"], list)

    def test_links_mode_has_content_key(
        self, register_tools, mock_web_client, sample_html
    ):
        """Links mode result should include a 'content' key (empty string)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()
        mock_web_client.get.return_value = mock_response

        mock_soup = MagicMock()
        mock_title_tag = MagicMock()
        mock_title_tag.get_text.return_value = "Test Page"
        mock_soup.find.return_value = mock_title_tag
        mock_web_client.parse_html.return_value = mock_soup
        mock_web_client.extract_links.return_value = [
            {"text": "Link", "url": "https://example.com"},
        ]

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://example.com/test", extract="links")

        assert result["success"] is True
        assert "links" in result
        assert "content" in result
        assert isinstance(result["content"], str)

    def test_non_html_has_links_key(self, register_tools, mock_web_client):
        """Non-HTML result should include a 'links' key."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.text = '{"data": 1}'
        mock_response.raise_for_status = MagicMock()
        mock_web_client.get.return_value = mock_response

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://api.example.com/data")

        assert result["success"] is True
        assert "content" in result
        assert "links" in result


# ===========================================================================
# Bug 1 regression: Error paths must have same keys as success paths
# ===========================================================================


class TestErrorReturnShapeConsistency:
    """Error results must include ALL keys that success results have.

    Before the fix, error paths returned bare dicts missing 'links', 'title',
    'content_type' — causing KeyError in consumers.
    """

    # Keys every read_webpage result (success or error) must have
    REQUIRED_KEYS = {
        "success",
        "url",
        "title",
        "content",
        "links",
        "content_type",
        "truncated",
    }

    def test_invalid_extract_mode_has_all_keys(self, register_tools):
        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://example.com", extract="bad_mode")
        assert result["success"] is False
        missing = self.REQUIRED_KEYS - set(result.keys())
        assert not missing, f"Error result missing keys: {missing}"

    def test_no_web_client_has_all_keys(self):
        """Error when _web_client is None should still have all keys."""
        from gaia.agents.tools.web_search import WebSearchMixin

        class NoClientAgent(WebSearchMixin):
            pass

        agent = NoClientAgent()
        agent._web_client = None  # deliberately no client
        agent.register_web_search_tools()

        read_webpage = _TOOL_REGISTRY["read_webpage"]["function"]
        result = read_webpage("https://example.com")
        assert result["success"] is False
        missing = self.REQUIRED_KEYS - set(result.keys())
        assert not missing, f"Error result missing keys: {missing}"

    def test_timeout_error_has_all_keys(self, register_tools, mock_web_client):
        mock_web_client.get.side_effect = requests.exceptions.Timeout("timed out")
        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://slow.example.com")
        assert result["success"] is False
        missing = self.REQUIRED_KEYS - set(result.keys())
        assert not missing, f"Error result missing keys: {missing}"

    def test_http_error_has_all_keys(self, register_tools, mock_web_client):
        mock_response = MagicMock()
        mock_response.status_code = 503
        http_error = requests.exceptions.HTTPError(
            "503 Service Unavailable", response=mock_response
        )
        mock_response.raise_for_status.side_effect = http_error
        mock_web_client.get.return_value = mock_response

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://down.example.com")
        assert result["success"] is False
        missing = self.REQUIRED_KEYS - set(result.keys())
        assert not missing, f"Error result missing keys: {missing}"

    def test_value_error_has_all_keys(self, register_tools, mock_web_client):
        mock_web_client.get.side_effect = ValueError("Blocked URL")
        read_webpage = register_tools("read_webpage")
        result = read_webpage("ftp://blocked.example.com")
        assert result["success"] is False
        missing = self.REQUIRED_KEYS - set(result.keys())
        assert not missing, f"Error result missing keys: {missing}"

    def test_generic_error_has_all_keys(self, register_tools, mock_web_client):
        mock_web_client.get.side_effect = RuntimeError("unexpected")
        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://broken.example.com")
        assert result["success"] is False
        missing = self.REQUIRED_KEYS - set(result.keys())
        assert not missing, f"Error result missing keys: {missing}"


# ===========================================================================
# Bug 2 regression: Double truncation for very large pages (>50500 chars)
# ===========================================================================


class TestDoubleTruncationLargePages:
    """Regression: pages larger than MAX_CONTENT_LENGTH + old headroom (500)
    were still getting double truncation. The fix uses MAX_CONTENT_LENGTH * 2
    so extract_text never truncates content that _truncate_content will handle.
    """

    def test_no_double_truncation_for_very_large_page(
        self, register_tools, mock_web_client
    ):
        """Simulate extract_text with a page far beyond MAX_CONTENT_LENGTH."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = "<html><body>huge page</body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_web_client.get.return_value = mock_response

        mock_soup = MagicMock()
        mock_title_tag = MagicMock()
        mock_title_tag.get_text.return_value = "Huge Page"
        mock_soup.find.return_value = mock_title_tag
        mock_web_client.parse_html.return_value = mock_soup

        # Simulate a page that's 80k chars — well beyond the old +500 headroom.
        # extract_text with old max_length=50500 would truncate and add suffix,
        # then _truncate_content would truncate AGAIN.  With the fix
        # (max_length=100000), extract_text won't truncate so only one suffix.
        huge_text = "word " * 16000  # 80k chars of words
        mock_web_client.extract_text.return_value = huge_text

        read_webpage = register_tools("read_webpage")
        result = read_webpage("https://example.com/huge", extract="text")

        assert result["success"] is True
        assert result["truncated"] is True
        # Only one truncation marker
        assert result["content"].count("...") == 1, (
            f"Expected exactly 1 '...' marker but found "
            f"{result['content'].count('...')}. "
            f"Tail: ...{result['content'][-80:]}"
        )


# ===========================================================================
# Bug 3 regression: Empty answer from Perplexity must return success=False
# ===========================================================================


class TestPerplexityEmptyAnswer:
    """Regression: _call_perplexity_api returned success=True when the API
    returned 200 OK but with empty answer — misleading for LLM consumers.
    """

    def test_empty_choices_returns_failure(self):
        """200 OK with empty choices array should be success=False."""
        from gaia.agents.tools.web_search import _call_perplexity_api

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [],
            "citations": [],
        }

        with patch(
            "gaia.agents.tools.web_search.requests.post", return_value=mock_response
        ):
            with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test-key"}):
                result = _call_perplexity_api("test query")

        assert result["success"] is False
        assert "no answer" in result["error"].lower()

    def test_empty_content_returns_failure(self):
        """200 OK with empty message content should be success=False."""
        from gaia.agents.tools.web_search import _call_perplexity_api

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": ""}}],
            "citations": ["https://example.com"],
        }

        with patch(
            "gaia.agents.tools.web_search.requests.post", return_value=mock_response
        ):
            with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test-key"}):
                result = _call_perplexity_api("test query")

        assert result["success"] is False
        assert "no answer" in result["error"].lower()
        # Citations should still be returned even on empty answer
        assert len(result["sources"]) > 0

    def test_whitespace_only_answer_returns_failure(self):
        """200 OK with whitespace-only answer should be success=False."""
        from gaia.agents.tools.web_search import _call_perplexity_api

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "   \n  "}}],
            "citations": [],
        }

        with patch(
            "gaia.agents.tools.web_search.requests.post", return_value=mock_response
        ):
            with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test-key"}):
                result = _call_perplexity_api("test query")

        assert result["success"] is False
        assert "no answer" in result["error"].lower()


# ===========================================================================
# Bug 4: Missing direct tests for _call_perplexity_api code paths
# ===========================================================================


class TestCallPerplexityApiMissingCoverage:
    """Tests for _call_perplexity_api code paths that had no coverage:
    timeout, connection error, and 401 invalid key.
    """

    def test_direct_api_timeout(self):
        """requests.exceptions.Timeout should be caught and return error."""
        from gaia.agents.tools.web_search import _call_perplexity_api

        with patch("gaia.agents.tools.web_search.requests.post") as mock_post:
            mock_post.side_effect = requests.exceptions.Timeout("read timed out")
            with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test-key"}):
                result = _call_perplexity_api("test query")

        assert result["success"] is False
        assert "timed out" in result["error"].lower()

    def test_direct_api_connection_error(self):
        """requests.exceptions.ConnectionError should be caught and return error."""
        from gaia.agents.tools.web_search import _call_perplexity_api

        with patch("gaia.agents.tools.web_search.requests.post") as mock_post:
            mock_post.side_effect = requests.exceptions.ConnectionError(
                "Connection refused"
            )
            with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test-key"}):
                result = _call_perplexity_api("test query")

        assert result["success"] is False
        assert "unavailable" in result["error"].lower()

    def test_direct_api_401_invalid_key(self):
        """401 status should return error about invalid API key."""
        from gaia.agents.tools.web_search import _call_perplexity_api

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch(
            "gaia.agents.tools.web_search.requests.post", return_value=mock_response
        ):
            with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "bad-key"}):
                result = _call_perplexity_api("test query")

        assert result["success"] is False
        assert (
            "invalid" in result["error"].lower() or "api key" in result["error"].lower()
        )
