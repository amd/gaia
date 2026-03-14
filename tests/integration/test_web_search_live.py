# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Live integration tests for web search tools.

These tests hit the real Perplexity API and are skipped if PERPLEXITY_API_KEY
is not set. They validate that the actual API integration works end-to-end.

Also includes local-only tests for the WebClient-based read_webpage
against a simple HTTP server.
"""

import http.server
import os
import threading
from unittest.mock import patch

import pytest

from gaia.agents.base.memory_mixin import MemoryMixin
from gaia.agents.base.shared_state import SharedAgentState
from gaia.agents.tools.web_search import (
    WebSearchMixin,
    _call_perplexity_api,
)

# ── Skip Conditions ──────────────────────────────────────────────────────────

_has_perplexity_key = bool(os.environ.get("PERPLEXITY_API_KEY"))

pytestmark_live = pytest.mark.skipif(
    not _has_perplexity_key,
    reason="PERPLEXITY_API_KEY not set — skipping live API tests",
)

try:
    import bs4  # noqa: F401

    _has_bs4 = True
except ImportError:
    _has_bs4 = False

pytestmark_bs4 = pytest.mark.skipif(
    not _has_bs4,
    reason="beautifulsoup4 not installed — skipping HTML parsing tests",
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_singleton():
    SharedAgentState._instance = None
    if hasattr(SharedAgentState, "_initialized"):
        delattr(SharedAgentState, "_initialized")
    yield
    SharedAgentState._instance = None
    if hasattr(SharedAgentState, "_initialized"):
        delattr(SharedAgentState, "_initialized")


@pytest.fixture(autouse=True)
def clean_tool_registry():
    from gaia.agents.base.tools import _TOOL_REGISTRY

    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


class _TestHost(MemoryMixin, WebSearchMixin):
    pass


def _make_host(workspace):
    SharedAgentState._instance = None
    if hasattr(SharedAgentState, "_initialized"):
        delattr(SharedAgentState, "_initialized")

    from gaia.agents.base.tools import _TOOL_REGISTRY

    _TOOL_REGISTRY.clear()

    host = _TestHost()
    host.init_memory(workspace_dir=workspace)

    # Initialize WebClient so read_webpage works
    from gaia.web.client import WebClient

    host._web_client = WebClient()
    return host


# ── Local HTTP Server for read_webpage tests ─────────────────────────────────


_TEST_HTML = """<!DOCTYPE html>
<html>
<head><title>Test Page</title></head>
<body>
<h1>Hello from GAIA Test Server</h1>
<p>This is a test page for web scraping integration tests.</p>
<a href="/page2">Link to page 2</a>
<a href="https://example.com">External link</a>
</body>
</html>"""


class _TestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(_TEST_HTML.encode())

    def log_message(self, format, *args):
        pass  # Suppress log output


@pytest.fixture(scope="module")
def local_server():
    """Start a local HTTP server for read_webpage tests."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _TestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


# ── Live Perplexity API Tests ────────────────────────────────────────────────


class TestPerplexityLive:
    """Tests that hit the real Perplexity API."""

    @pytestmark_live
    def test_web_search_returns_results(self):
        """Live Perplexity API returns results with answer and sources."""
        result = _call_perplexity_api("What is AMD Ryzen AI?")
        assert result is not None
        assert "answer" in result
        assert len(result["answer"]) > 0
        assert "sources" in result

    @pytestmark_live
    def test_web_search_empty_query_handled(self):
        """Empty or very short query doesn't crash."""
        result = _call_perplexity_api("")
        # Should return None or error, not crash
        assert result is None or "error" in result or "answer" in result


# ── WebSearchMixin Integration (Mocked API) ──────────────────────────────────


class TestWebSearchMixinIntegration:
    """Integration tests for WebSearchMixin with mocked Perplexity but real DB."""

    @patch("gaia.agents.tools.web_search._call_perplexity_api")
    def test_web_search_stores_result_context(self, mock_api, workspace):
        """Web search result can be stored as knowledge for later recall."""
        mock_api.return_value = {
            "success": True,
            "answer": "AMD Ryzen AI processors feature dedicated NPU cores.",
            "sources": ["https://amd.com/ryzen-ai"],
        }

        host = _make_host(workspace)
        host.register_web_search_tools()

        from gaia.agents.base.tools import _TOOL_REGISTRY

        search_tool = _TOOL_REGISTRY.get("web_search")
        assert search_tool is not None
        result = search_tool["function"](query="AMD Ryzen AI NPU")
        assert result["success"] is True
        assert "NPU" in result["answer"]

        # Store the result as knowledge
        host.knowledge.store_insight(
            category="fact",
            content=result["answer"],
            domain="technology",
        )

        recalled = host.knowledge.recall(query="Ryzen NPU cores")
        assert len(recalled) >= 1

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()


# ── read_webpage with Local Server ───────────────────────────────────────────


@pytest.mark.skipif(not _has_bs4, reason="beautifulsoup4 not installed")
class TestReadWebpageLocal:
    """Test read_webpage against a local HTTP server.

    The WebClient blocks 127.0.0.1 by default (SSRF protection), so we
    patch validate_url to allow our local test server.
    """

    def test_read_webpage_text(self, workspace, local_server):
        """read_webpage extracts text content from a page."""
        host = _make_host(workspace)
        host.register_web_search_tools()

        from gaia.agents.base.tools import _TOOL_REGISTRY

        read_tool = _TOOL_REGISTRY.get("read_webpage")
        assert read_tool is not None

        with patch.object(host._web_client, "validate_url"):
            result = read_tool["function"](url=local_server, extract="text")
        assert result["success"] is True
        assert "Hello from GAIA Test Server" in result["content"]

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_read_webpage_links(self, workspace, local_server):
        """read_webpage extracts links from a page."""
        host = _make_host(workspace)
        host.register_web_search_tools()

        from gaia.agents.base.tools import _TOOL_REGISTRY

        read_tool = _TOOL_REGISTRY.get("read_webpage")
        assert read_tool is not None

        with patch.object(host._web_client, "validate_url"):
            result = read_tool["function"](url=local_server, extract="links")
        assert result["success"] is True
        # Links are [{"text": str, "url": str}]
        assert any("example.com" in link["url"] for link in result["links"])

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_read_webpage_full(self, workspace, local_server):
        """read_webpage in full mode returns full text."""
        host = _make_host(workspace)
        host.register_web_search_tools()

        from gaia.agents.base.tools import _TOOL_REGISTRY

        read_tool = _TOOL_REGISTRY.get("read_webpage")
        assert read_tool is not None

        with patch.object(host._web_client, "validate_url"):
            result = read_tool["function"](url=local_server, extract="full")
        assert result["success"] is True
        assert "Hello from GAIA Test Server" in result["content"]

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_read_webpage_invalid_url(self, workspace):
        """read_webpage handles invalid URLs gracefully."""
        host = _make_host(workspace)
        host.register_web_search_tools()

        from gaia.agents.base.tools import _TOOL_REGISTRY

        read_tool = _TOOL_REGISTRY.get("read_webpage")
        result = read_tool["function"](
            url="http://127.0.0.1:1", extract="text"
        )
        assert result["success"] is False

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()


# ── Error Path Tests ─────────────────────────────────────────────────────────


class TestWebSearchErrorPaths:
    """Tests for error handling and edge cases in web search tools."""

    def test_read_webpage_ssrf_blocked(self, workspace):
        """SSRF protection blocks requests to loopback addresses without patching."""
        from gaia.web.client import WebClient

        host = _TestHost()
        host.init_memory(workspace_dir=workspace)
        host._web_client = WebClient()
        host.register_web_search_tools()

        from gaia.agents.base.tools import _TOOL_REGISTRY

        read_tool = _TOOL_REGISTRY.get("read_webpage")
        assert read_tool is not None

        # Call WITHOUT patching validate_url -- SSRF protection should block it
        result = read_tool["function"](url="http://127.0.0.1:1234", extract="text")
        assert result["success"] is False
        # The error should indicate the URL was blocked (ValueError from validate_url)
        assert "error" in result

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_web_search_tool_registration(self, workspace):
        """register_web_search_tools() adds both tools to the registry."""
        host = _make_host(workspace)
        host.register_web_search_tools()

        from gaia.agents.base.tools import _TOOL_REGISTRY

        assert "web_search" in _TOOL_REGISTRY, (
            "web_search tool should be in _TOOL_REGISTRY after registration"
        )
        assert "read_webpage" in _TOOL_REGISTRY, (
            "read_webpage tool should be in _TOOL_REGISTRY after registration"
        )

        # Verify they are callable
        assert callable(_TOOL_REGISTRY["web_search"]["function"])
        assert callable(_TOOL_REGISTRY["read_webpage"]["function"])

        host._shared_state.memory.close()
        host._shared_state.knowledge.close()

    def test_read_webpage_without_web_client(self, workspace):
        """read_webpage fails gracefully when _web_client is not set."""
        host = _TestHost()
        host.init_memory(workspace_dir=workspace)
        # Intentionally do NOT set host._web_client
        assert host._web_client is None
        host.register_web_search_tools()

        from gaia.agents.base.tools import _TOOL_REGISTRY

        read_tool = _TOOL_REGISTRY.get("read_webpage")
        assert read_tool is not None

        result = read_tool["function"](url="https://example.com", extract="text")
        assert result["success"] is False
        assert "error" in result
        assert (
            "not initialized" in result["error"].lower()
            or "web client" in result["error"].lower()
        )
