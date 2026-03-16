# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for WebClient and BrowserToolsMixin."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig
from gaia.web.client import WebClient

# ===== WebClient Tests =====


class TestWebClientURLValidation:
    """Test URL validation and SSRF prevention."""

    def setup_method(self):
        self.client = WebClient()

    def teardown_method(self):
        self.client.close()

    def test_valid_http_url(self):
        """Accept valid HTTP URLs."""
        with patch.object(self.client, "_validate_host_ip"):
            result = self.client.validate_url("http://example.com")
            assert result == "http://example.com"

    def test_valid_https_url(self):
        """Accept valid HTTPS URLs."""
        with patch.object(self.client, "_validate_host_ip"):
            result = self.client.validate_url("https://example.com/page")
            assert result == "https://example.com/page"

    def test_blocked_scheme_ftp(self):
        """Block FTP scheme."""
        with pytest.raises(ValueError, match="Blocked URL scheme"):
            self.client.validate_url("ftp://example.com/file")

    def test_blocked_scheme_file(self):
        """Block file:// scheme."""
        with pytest.raises(ValueError, match="Blocked URL scheme"):
            self.client.validate_url("file:///etc/passwd")

    def test_blocked_scheme_javascript(self):
        """Block javascript: scheme."""
        with pytest.raises(ValueError, match="Blocked URL scheme"):
            self.client.validate_url("javascript:alert(1)")

    def test_blocked_port_ssh(self):
        """Block SSH port 22."""
        with pytest.raises(ValueError, match="Blocked port"):
            self.client.validate_url("http://example.com:22/path")

    def test_blocked_port_mysql(self):
        """Block MySQL port 3306."""
        with pytest.raises(ValueError, match="Blocked port"):
            self.client.validate_url("http://example.com:3306/db")

    def test_no_hostname(self):
        """Block URLs without hostname."""
        with pytest.raises(ValueError, match="no hostname"):
            self.client.validate_url("http://")

    def test_private_ip_blocked(self):
        """Block private IP addresses (192.168.x.x)."""
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (2, 1, 6, "", ("192.168.1.1", 0)),
            ]
            with pytest.raises(ValueError, match="private/reserved IP"):
                self.client.validate_url("http://internal.example.com")

    def test_loopback_blocked(self):
        """Block localhost/loopback addresses."""
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (2, 1, 6, "", ("127.0.0.1", 0)),
            ]
            with pytest.raises(ValueError, match="private/reserved IP"):
                self.client.validate_url("http://localhost")

    def test_link_local_blocked(self):
        """Block link-local addresses (cloud metadata)."""
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (2, 1, 6, "", ("169.254.169.254", 0)),
            ]
            with pytest.raises(ValueError, match="private/reserved IP"):
                self.client.validate_url("http://metadata.google.internal")

    def test_unresolvable_hostname(self):
        """Handle DNS resolution failure."""
        import socket

        with patch("socket.getaddrinfo", side_effect=socket.gaierror("Not found")):
            with pytest.raises(ValueError, match="Cannot resolve hostname"):
                self.client.validate_url("http://nonexistent.invalid")


class TestWebClientSanitizeFilename:
    """Test filename sanitization for downloads."""

    def test_normal_filename(self):
        assert WebClient._sanitize_filename("report.pdf") == "report.pdf"

    def test_path_traversal(self):
        result = WebClient._sanitize_filename("../../etc/passwd")
        assert "/" not in result
        assert "\\" not in result
        assert result == "passwd"

    def test_null_bytes(self):
        result = WebClient._sanitize_filename("file\x00.txt")
        assert "\x00" not in result

    def test_hidden_file(self):
        result = WebClient._sanitize_filename(".htaccess")
        assert not result.startswith(".")
        assert result == "_.htaccess"

    def test_special_characters(self):
        result = WebClient._sanitize_filename("my file (2).pdf")
        # Only safe chars remain
        assert all(
            c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
            for c in result
        )

    def test_empty_becomes_download(self):
        assert WebClient._sanitize_filename("") == "download"

    def test_long_filename_truncated(self):
        long_name = "a" * 300 + ".pdf"
        result = WebClient._sanitize_filename(long_name)
        assert len(result) <= 200


class TestWebClientRateLimiting:
    """Test per-domain rate limiting."""

    def setup_method(self):
        self.client = WebClient(rate_limit=0.1)  # Short for testing

    def teardown_method(self):
        self.client.close()

    def test_rate_limit_tracks_domains(self):
        """Rate limit state is per-domain."""
        self.client._rate_limit_wait("example.com")
        assert "example.com" in self.client._domain_last_request

    def test_different_domains_independent(self):
        """Different domains don't share rate limit state."""
        self.client._rate_limit_wait("a.com")
        self.client._rate_limit_wait("b.com")
        assert "a.com" in self.client._domain_last_request
        assert "b.com" in self.client._domain_last_request


class TestWebClientHTMLExtraction:
    """Test HTML content extraction."""

    def setup_method(self):
        self.client = WebClient()

    def teardown_method(self):
        self.client.close()

    @pytest.fixture(autouse=True)
    def check_bs4(self):
        """Skip if BeautifulSoup not available."""
        try:
            from bs4 import BeautifulSoup  # noqa: F401
        except ImportError:
            pytest.skip("beautifulsoup4 not installed")

    def test_extract_text_headings(self):
        """Headings are preserved with formatting."""
        html = "<html><body><h1>Title</h1><p>Body text here.</p></body></html>"
        soup = self.client.parse_html(html)
        text = self.client.extract_text(soup)
        assert "Title" in text
        assert "Body text here." in text

    def test_extract_text_removes_scripts(self):
        """Script tags are removed."""
        html = '<html><body><p>Visible</p><script>alert("xss")</script></body></html>'
        soup = self.client.parse_html(html)
        text = self.client.extract_text(soup)
        assert "Visible" in text
        assert "alert" not in text

    def test_extract_text_removes_nav(self):
        """Navigation is removed."""
        html = "<html><body><nav>Menu items</nav><p>Content here.</p></body></html>"
        soup = self.client.parse_html(html)
        text = self.client.extract_text(soup)
        assert "Content here." in text
        assert "Menu items" not in text

    def test_extract_text_truncation(self):
        """Text is truncated at max_length."""
        html = "<html><body><p>" + "word " * 2000 + "</p></body></html>"
        soup = self.client.parse_html(html)
        text = self.client.extract_text(soup, max_length=100)
        assert len(text) <= 120  # Slight overshoot for truncation message
        assert "truncated" in text

    def test_extract_tables_basic(self):
        """Extract a basic HTML table."""
        html = """
        <html><body>
        <table>
            <tr><th>Name</th><th>Value</th></tr>
            <tr><td>Alpha</td><td>100</td></tr>
            <tr><td>Beta</td><td>200</td></tr>
        </table>
        </body></html>
        """
        soup = self.client.parse_html(html)
        tables = self.client.extract_tables(soup)
        assert len(tables) == 1
        assert len(tables[0]["data"]) == 2
        assert tables[0]["data"][0]["Name"] == "Alpha"
        assert tables[0]["data"][1]["Value"] == "200"

    def test_extract_tables_skips_single_row(self):
        """Skip tables with only one row (likely layout)."""
        html = """
        <html><body>
        <table><tr><td>Single row</td></tr></table>
        </body></html>
        """
        soup = self.client.parse_html(html)
        tables = self.client.extract_tables(soup)
        assert len(tables) == 0

    def test_extract_links(self):
        """Extract links with text and resolved URLs."""
        html = """
        <html><body>
        <a href="/page1">Page One</a>
        <a href="https://other.com/page2">Page Two</a>
        <a href="#section">Anchor Only</a>
        </body></html>
        """
        soup = self.client.parse_html(html)
        links = self.client.extract_links(soup, "https://example.com")
        # Should have 2 links (anchor-only skipped)
        assert len(links) == 2
        assert links[0]["text"] == "Page One"
        assert links[0]["url"] == "https://example.com/page1"
        assert links[1]["url"] == "https://other.com/page2"

    def test_extract_links_deduplication(self):
        """Duplicate links are removed."""
        html = """
        <html><body>
        <a href="/page">Link 1</a>
        <a href="/page">Link 2</a>
        </body></html>
        """
        soup = self.client.parse_html(html)
        links = self.client.extract_links(soup, "https://example.com")
        assert len(links) == 1


class TestWebClientDuckDuckGo:
    """Test DuckDuckGo search parsing."""

    def setup_method(self):
        self.client = WebClient()

    def teardown_method(self):
        self.client.close()

    @pytest.fixture(autouse=True)
    def check_bs4(self):
        try:
            from bs4 import BeautifulSoup  # noqa: F401
        except ImportError:
            pytest.skip("beautifulsoup4 not installed")

    def test_parse_ddg_results(self):
        """Parse DuckDuckGo search result HTML."""
        mock_html = """
        <html><body>
        <div class="result">
            <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage">
                Example Result
            </a>
            <a class="result__snippet">This is a snippet about the result.</a>
        </div>
        <div class="result">
            <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fother.com">
                Other Result
            </a>
            <a class="result__snippet">Another snippet.</a>
        </div>
        </body></html>
        """
        mock_response = MagicMock()
        mock_response.text = mock_html
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.encoding = "utf-8"
        mock_response.apparent_encoding = "utf-8"

        with patch.object(self.client, "_request", return_value=mock_response):
            results = self.client.search_duckduckgo("test query", num_results=5)

        assert len(results) == 2
        assert results[0]["title"] == "Example Result"
        assert results[0]["url"] == "https://example.com/page"
        assert results[1]["title"] == "Other Result"


class TestWebClientDownload:
    """Test file download functionality."""

    def setup_method(self):
        self.client = WebClient()

    def teardown_method(self):
        self.client.close()

    def test_download_streams_to_disk(self):
        """Download streams content to disk."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "Content-Type": "application/pdf",
            "Content-Length": "1024",
        }
        mock_response.iter_content.return_value = [b"x" * 1024]

        with (
            patch.object(self.client, "validate_url"),
            patch.object(self.client, "_rate_limit_wait"),
            patch.object(self.client._session, "get", return_value=mock_response),
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                result = self.client.download(
                    "https://example.com/file.pdf",
                    save_dir=tmpdir,
                )
                assert result["size"] == 1024
                assert result["filename"] == "file.pdf"
                assert os.path.exists(result["path"])

    def test_download_sanitizes_filename(self):
        """Downloaded filenames are sanitized."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "Content-Type": "text/plain",
            "Content-Disposition": 'attachment; filename="../../etc/passwd"',
        }
        mock_response.iter_content.return_value = [b"test"]

        with (
            patch.object(self.client, "validate_url"),
            patch.object(self.client, "_rate_limit_wait"),
            patch.object(self.client._session, "get", return_value=mock_response),
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                result = self.client.download(
                    "https://example.com/file",
                    save_dir=tmpdir,
                )
                # Should not contain path traversal
                assert ".." not in result["filename"]
                assert "/" not in result["filename"]


# ===== BrowserToolsMixin Tests =====


class TestBrowserToolsMixin:
    """Test the BrowserToolsMixin tool registration and behavior."""

    def setup_method(self):
        """Create a mock agent with BrowserToolsMixin."""
        from gaia.agents.tools.browser_tools import BrowserToolsMixin

        class MockAgent(BrowserToolsMixin):
            def __init__(self):
                self._web_client = None
                self._path_validator = None
                self._tools = {}

        # Patch the tool decorator to capture registered tools
        self.registered_tools = {}

        def mock_tool(atomic=True):
            def decorator(func):
                self.registered_tools[func.__name__] = func
                return func

            return decorator

        with patch("gaia.agents.base.tools.tool", mock_tool):
            self.agent = MockAgent()
            self.agent.register_browser_tools()

    def test_tools_registered(self):
        """All 3 browser tools should be registered."""
        assert "fetch_page" in self.registered_tools
        assert "search_web" in self.registered_tools
        assert "download_file" in self.registered_tools
        assert len(self.registered_tools) == 3

    def test_fetch_page_no_client(self):
        """fetch_page returns error when web client not initialized."""
        result = self.registered_tools["fetch_page"]("https://example.com")
        assert "Error" in result
        assert "not initialized" in result

    def test_search_web_no_client(self):
        """search_web returns error when web client not initialized."""
        result = self.registered_tools["search_web"]("test query")
        assert "Error" in result
        assert "not initialized" in result

    def test_download_file_no_client(self):
        """download_file returns error when web client not initialized."""
        result = self.registered_tools["download_file"]("https://example.com/file.pdf")
        assert "Error" in result
        assert "not initialized" in result

    def test_fetch_page_invalid_extract_mode(self):
        """fetch_page rejects invalid extract modes."""
        self.agent._web_client = MagicMock()
        result = self.registered_tools["fetch_page"](
            "https://example.com", extract="invalid"
        )
        assert "Error" in result
        assert "invalid" in result.lower()

    def test_fetch_page_clamps_max_length(self):
        """fetch_page clamps max_length to valid range."""
        self.agent._web_client = MagicMock()

        mock_response = MagicMock()
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = "<html><body><p>Hello</p></body></html>"
        mock_response.raise_for_status = MagicMock()
        self.agent._web_client.get.return_value = mock_response

        mock_soup = MagicMock()
        title_tag = MagicMock()
        title_tag.get_text.return_value = "Test"
        mock_soup.find.return_value = title_tag
        self.agent._web_client.parse_html.return_value = mock_soup
        self.agent._web_client.extract_text.return_value = "Hello"

        # max_length=99999 should be clamped to 20000
        result = self.registered_tools["fetch_page"](
            "https://example.com", max_length=99999
        )
        self.agent._web_client.extract_text.assert_called_once()
        call_kwargs = self.agent._web_client.extract_text.call_args
        assert call_kwargs[1]["max_length"] == 20000

    def test_search_web_clamps_num_results(self):
        """search_web clamps num_results to valid range."""
        self.agent._web_client = MagicMock()
        self.agent._web_client.search_duckduckgo.return_value = [
            {"title": "Test", "url": "https://test.com", "snippet": "A test"}
        ]

        result = self.registered_tools["search_web"]("test", num_results=100)
        # Should have been clamped to 10
        self.agent._web_client.search_duckduckgo.assert_called_once_with(
            "test", num_results=10
        )

    def test_download_file_formats_size(self):
        """download_file formats file sizes correctly."""
        self.agent._web_client = MagicMock()
        self.agent._web_client.download.return_value = {
            "filename": "report.pdf",
            "path": "/tmp/report.pdf",
            "size": 2_500_000,
            "content_type": "application/pdf",
        }

        result = self.registered_tools["download_file"](
            "https://example.com/report.pdf"
        )
        assert "2.4 MB" in result
        assert "report.pdf" in result


# ===== WebClient Redirect Tests =====


class TestWebClientRedirects:
    """Test manual redirect following with SSRF validation at each hop."""

    def setup_method(self):
        self.client = WebClient()

    def teardown_method(self):
        self.client.close()

    def test_follows_redirect_and_validates_each_hop(self):
        """Each redirect hop is validated for SSRF."""
        # First response: 302 redirect
        redirect_response = MagicMock()
        redirect_response.status_code = 302
        redirect_response.headers = {
            "Location": "https://cdn.example.com/page",
            "Content-Length": "0",
        }

        # Final response: 200 OK
        final_response = MagicMock()
        final_response.status_code = 200
        final_response.headers = {"Content-Type": "text/html", "Content-Length": "100"}
        final_response.encoding = "utf-8"
        final_response.apparent_encoding = "utf-8"
        final_response.text = "<html>OK</html>"

        self.client._session.request = MagicMock(
            side_effect=[redirect_response, final_response]
        )

        mock_validate = MagicMock(side_effect=lambda url: url)
        self.client.validate_url = mock_validate

        result = self.client.get("https://example.com/old")

        assert result.status_code == 200
        # validate_url called for original + redirect target
        assert mock_validate.call_count == 2

    def test_redirect_to_private_ip_blocked(self):
        """Redirect to private IP is blocked at the hop."""
        redirect_response = MagicMock()
        redirect_response.status_code = 302
        redirect_response.headers = {
            "Location": "http://192.168.1.1/admin",
            "Content-Length": "0",
        }

        self.client._session.request = MagicMock(return_value=redirect_response)

        # First call passes, second call (redirect target) raises
        call_count = [0]
        original_validate = self.client.validate_url

        def validate_side_effect(url):
            call_count[0] += 1
            if call_count[0] == 1:
                return url  # Allow original
            raise ValueError("Blocked: private IP")

        with patch.object(
            self.client, "validate_url", side_effect=validate_side_effect
        ):
            with pytest.raises(ValueError, match="private IP"):
                self.client.get("https://example.com/redirect")

    def test_max_redirects_exceeded(self):
        """Too many redirects raises ValueError."""
        redirect_response = MagicMock()
        redirect_response.status_code = 302
        redirect_response.headers = {
            "Location": "https://example.com/loop",
            "Content-Length": "0",
        }

        self.client._session.request = MagicMock(return_value=redirect_response)

        with patch.object(self.client, "validate_url"):
            with pytest.raises(ValueError, match="Too many redirects"):
                self.client.get("https://example.com/loop")

    def test_301_302_303_downgrades_to_get(self):
        """POST redirected via 301/302/303 becomes GET."""
        redirect_response = MagicMock()
        redirect_response.status_code = 303
        redirect_response.headers = {
            "Location": "https://example.com/result",
            "Content-Length": "0",
        }

        final_response = MagicMock()
        final_response.status_code = 200
        final_response.headers = {"Content-Type": "text/html", "Content-Length": "10"}
        final_response.encoding = "utf-8"
        final_response.apparent_encoding = "utf-8"

        calls = []

        def track_request(method, url, **kwargs):
            calls.append(method)
            if len(calls) == 1:
                return redirect_response
            return final_response

        self.client._session.request = track_request

        with patch.object(self.client, "validate_url"):
            self.client.post("https://example.com/form", data={"key": "val"})

        assert calls[0] == "POST"
        assert calls[1] == "GET"


class TestWebClientResponseSizeLimits:
    """Test response size enforcement."""

    def setup_method(self):
        self.client = WebClient(max_response_size=1000)

    def teardown_method(self):
        self.client.close()

    def test_rejects_oversized_response(self):
        """Response with Content-Length exceeding max is rejected."""
        oversized_response = MagicMock()
        oversized_response.status_code = 200
        oversized_response.headers = {"Content-Length": "999999"}

        self.client._session.request = MagicMock(return_value=oversized_response)

        with patch.object(self.client, "validate_url"):
            with pytest.raises(ValueError, match="Response too large"):
                self.client.get("https://example.com/big")


class TestWebClientDownloadEdgeCases:
    """Additional download edge case tests."""

    def setup_method(self):
        self.client = WebClient(max_download_size=500)

    def teardown_method(self):
        self.client.close()

    def test_download_exceeds_max_size_during_stream(self):
        """Download that exceeds max size during streaming is aborted."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/octet-stream"}
        mock_response.raise_for_status = MagicMock()
        # Send chunks that total > 500 bytes
        mock_response.iter_content.return_value = [b"x" * 300, b"x" * 300]

        with (
            patch.object(self.client, "validate_url"),
            patch.object(self.client, "_rate_limit_wait"),
            patch.object(self.client._session, "get", return_value=mock_response),
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                with pytest.raises(ValueError, match="exceeded max size"):
                    self.client.download("https://example.com/big.bin", save_dir=tmpdir)

    def test_download_content_length_too_large(self):
        """Download rejected before streaming if Content-Length too large."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "Content-Type": "application/zip",
            "Content-Length": "999999",
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch.object(self.client, "validate_url"),
            patch.object(self.client, "_rate_limit_wait"),
            patch.object(self.client._session, "get", return_value=mock_response),
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                with pytest.raises(ValueError, match="File too large"):
                    self.client.download(
                        "https://example.com/huge.zip", save_dir=tmpdir
                    )


# ===== BrowserToolsMixin Happy Path Tests =====


class TestBrowserToolsMixinHappyPaths:
    """Test BrowserToolsMixin tools with working WebClient mock."""

    def setup_method(self):
        from gaia.agents.tools.browser_tools import BrowserToolsMixin

        class MockAgent(BrowserToolsMixin):
            def __init__(self):
                self._web_client = MagicMock()
                self._path_validator = None
                self._tools = {}

        self.registered_tools = {}

        def mock_tool(atomic=True):
            def decorator(func):
                self.registered_tools[func.__name__] = func
                return func

            return decorator

        with patch("gaia.agents.base.tools.tool", mock_tool):
            self.agent = MockAgent()
            self.agent.register_browser_tools()

    def test_fetch_page_text_mode(self):
        """fetch_page returns formatted text content."""
        mock_response = MagicMock()
        mock_response.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_response.text = "<html><body><p>Hello World</p></body></html>"
        mock_response.raise_for_status = MagicMock()
        self.agent._web_client.get.return_value = mock_response

        mock_soup = MagicMock()
        title_tag = MagicMock()
        title_tag.get_text.return_value = "Test Page"
        mock_soup.find.return_value = title_tag
        self.agent._web_client.parse_html.return_value = mock_soup
        self.agent._web_client.extract_text.return_value = "Hello World"

        result = self.registered_tools["fetch_page"]("https://example.com")
        assert "Page: Test Page" in result
        assert "URL: https://example.com" in result
        assert "Hello World" in result

    def test_fetch_page_json_content(self):
        """fetch_page returns JSON content directly for API responses."""
        mock_response = MagicMock()
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.text = '{"key": "value", "count": 42}'
        mock_response.raise_for_status = MagicMock()
        self.agent._web_client.get.return_value = mock_response

        result = self.registered_tools["fetch_page"]("https://api.example.com/data")
        assert "application/json" in result
        assert '{"key": "value"' in result

    def test_fetch_page_binary_suggests_download(self):
        """fetch_page suggests download_file for binary content."""
        mock_response = MagicMock()
        mock_response.headers = {
            "Content-Type": "application/pdf",
            "Content-Length": "5000000",
        }
        mock_response.raise_for_status = MagicMock()
        self.agent._web_client.get.return_value = mock_response

        result = self.registered_tools["fetch_page"]("https://example.com/doc.pdf")
        assert "download_file" in result
        assert "binary content" in result

    def test_fetch_page_tables_mode(self):
        """fetch_page tables mode returns JSON-formatted table data."""
        mock_response = MagicMock()
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = "<html></html>"
        mock_response.raise_for_status = MagicMock()
        self.agent._web_client.get.return_value = mock_response

        mock_soup = MagicMock()
        title_tag = MagicMock()
        title_tag.get_text.return_value = "Pricing Page"
        mock_soup.find.return_value = title_tag
        self.agent._web_client.parse_html.return_value = mock_soup
        self.agent._web_client.extract_tables.return_value = [
            {
                "table_name": "Plans",
                "data": [{"plan": "Basic", "price": "$10"}],
            }
        ]

        result = self.registered_tools["fetch_page"](
            "https://example.com/pricing", extract="tables"
        )
        assert "Pricing Page" in result
        assert "Plans" in result
        assert "Basic" in result

    def test_fetch_page_links_mode(self):
        """fetch_page links mode returns formatted link list."""
        mock_response = MagicMock()
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = "<html></html>"
        mock_response.raise_for_status = MagicMock()
        self.agent._web_client.get.return_value = mock_response

        mock_soup = MagicMock()
        title_tag = MagicMock()
        title_tag.get_text.return_value = "Links Page"
        mock_soup.find.return_value = title_tag
        self.agent._web_client.parse_html.return_value = mock_soup
        self.agent._web_client.extract_links.return_value = [
            {"text": "Home", "url": "https://example.com/"},
            {"text": "About", "url": "https://example.com/about"},
        ]

        result = self.registered_tools["fetch_page"](
            "https://example.com", extract="links"
        )
        assert "Links: 2" in result
        assert "Home" in result
        assert "About" in result

    def test_fetch_page_url_validation_error(self):
        """fetch_page handles URL validation errors gracefully."""
        self.agent._web_client.get.side_effect = ValueError(
            "Blocked: resolves to private IP"
        )

        result = self.registered_tools["fetch_page"]("http://192.168.1.1/admin")
        assert "Error" in result
        assert "private IP" in result

    def test_search_web_no_results(self):
        """search_web handles empty results gracefully."""
        self.agent._web_client.search_duckduckgo.return_value = []

        result = self.registered_tools["search_web"]("xyzzy nonexistent query 12345")
        assert "No results found" in result

    def test_search_web_formats_results(self):
        """search_web formats results with numbering."""
        self.agent._web_client.search_duckduckgo.return_value = [
            {
                "title": "Python Docs",
                "url": "https://docs.python.org",
                "snippet": "Official Python documentation",
            },
            {
                "title": "Real Python",
                "url": "https://realpython.com",
                "snippet": "Python tutorials",
            },
        ]

        result = self.registered_tools["search_web"]("python tutorial")
        assert "1. Python Docs" in result
        assert "2. Real Python" in result
        assert "https://docs.python.org" in result
        assert "fetch_page" in result  # Should suggest fetching

    def test_search_web_network_error(self):
        """search_web handles network errors gracefully."""
        self.agent._web_client.search_duckduckgo.side_effect = Exception(
            "Connection timeout"
        )

        result = self.registered_tools["search_web"]("test")
        assert "Error" in result
        assert "fetch_page" in result  # Should suggest alternative

    def test_download_file_network_error(self):
        """download_file handles network errors gracefully."""
        self.agent._web_client.download.side_effect = Exception("Connection refused")

        result = self.registered_tools["download_file"]("https://example.com/file.pdf")
        assert "Error" in result
        assert "Connection refused" in result

    def test_download_file_size_formatting_kb(self):
        """download_file formats KB sizes correctly."""
        self.agent._web_client.download.return_value = {
            "filename": "small.txt",
            "path": "/tmp/small.txt",
            "size": 2048,
            "content_type": "text/plain",
        }

        result = self.registered_tools["download_file"]("https://example.com/small.txt")
        assert "2.0 KB" in result

    def test_download_file_size_formatting_bytes(self):
        """download_file formats byte sizes correctly."""
        self.agent._web_client.download.return_value = {
            "filename": "tiny.txt",
            "path": "/tmp/tiny.txt",
            "size": 512,
            "content_type": "text/plain",
        }

        result = self.registered_tools["download_file"]("https://example.com/tiny.txt")
        assert "512 bytes" in result


# ===== ChatAgent Integration Tests =====


class TestChatAgentBrowserIntegration:
    """Test ChatAgent initializes and registers browser tools correctly."""

    def test_web_client_initialized_when_enabled(self):
        """ChatAgent creates WebClient when enable_browser=True."""
        config = ChatAgentConfig(
            silent_mode=True,
            enable_browser=True,
            enable_filesystem=False,
            enable_scratchpad=False,
        )
        with (
            patch("gaia.agents.chat.agent.RAGSDK"),
            patch("gaia.agents.chat.agent.RAGConfig"),
        ):
            agent = ChatAgent(config)
        assert agent._web_client is not None
        agent._web_client.close()

    def test_web_client_none_when_disabled(self):
        """ChatAgent skips WebClient when enable_browser=False."""
        config = ChatAgentConfig(
            silent_mode=True,
            enable_browser=False,
            enable_filesystem=False,
            enable_scratchpad=False,
        )
        with (
            patch("gaia.agents.chat.agent.RAGSDK"),
            patch("gaia.agents.chat.agent.RAGConfig"),
        ):
            agent = ChatAgent(config)
        assert agent._web_client is None

    def test_browser_config_fields_passed_to_webclient(self):
        """ChatAgent passes browser config to WebClient."""
        config = ChatAgentConfig(
            silent_mode=True,
            enable_browser=True,
            browser_timeout=60,
            browser_max_download_size=50 * 1024 * 1024,
            browser_rate_limit=2.0,
            enable_filesystem=False,
            enable_scratchpad=False,
        )
        with (
            patch("gaia.agents.chat.agent.RAGSDK"),
            patch("gaia.agents.chat.agent.RAGConfig"),
        ):
            agent = ChatAgent(config)
        assert agent._web_client._timeout == 60
        assert agent._web_client._max_download_size == 50 * 1024 * 1024
        assert agent._web_client._rate_limit == 2.0
        agent._web_client.close()

    def test_browser_tools_in_registered_tools(self):
        """ChatAgent registers browser tools alongside other tools."""
        config = ChatAgentConfig(
            silent_mode=True,
            enable_browser=True,
            enable_filesystem=False,
            enable_scratchpad=False,
        )
        with (
            patch("gaia.agents.chat.agent.RAGSDK"),
            patch("gaia.agents.chat.agent.RAGConfig"),
        ):
            agent = ChatAgent(config)

        tool_names = list(agent.get_tools_info().keys())
        assert "fetch_page" in tool_names
        assert "search_web" in tool_names
        assert "download_file" in tool_names
        if agent._web_client:
            agent._web_client.close()

    def test_system_prompt_includes_browser_section(self):
        """ChatAgent system prompt mentions browser tools."""
        config = ChatAgentConfig(
            silent_mode=True,
            enable_browser=True,
            enable_filesystem=False,
            enable_scratchpad=False,
        )
        with (
            patch("gaia.agents.chat.agent.RAGSDK"),
            patch("gaia.agents.chat.agent.RAGConfig"),
        ):
            agent = ChatAgent(config)

        prompt = agent._get_system_prompt()
        assert "fetch_page" in prompt
        assert "search_web" in prompt
        assert "download_file" in prompt
        assert "BROWSER TOOLS" in prompt
        if agent._web_client:
            agent._web_client.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
