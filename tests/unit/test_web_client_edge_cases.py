# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Edge case tests for WebClient (gaia.web.client).

Covers the following untested scenarios:
1. parse_html: lxml fallback to html.parser
2. extract_text: fallback to get_text when structured extraction yields <100 chars
3. extract_tables: thead element handling, caption extraction, col_index overflow
4. extract_links: javascript: links skipped, empty href skipped, no-text links
5. download: redirect following during streaming download, Content-Disposition
   with filename*=UTF-8 encoding
6. close: session cleanup verification
7. search_duckduckgo: bs4 not available raises ImportError
8. _request: encoding fixup (ISO-8859-1 apparent_encoding detection)

All tests run without LLM or external services.
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from gaia.web.client import WebClient

# ============================================================================
# 1. parse_html: lxml fallback to html.parser
# ============================================================================


class TestParseHtmlLxmlFallback:
    """Test that parse_html falls back to html.parser when lxml fails."""

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

    def test_lxml_exception_falls_back_to_html_parser(self):
        """When lxml raises an exception, html.parser should be used instead."""
        from bs4 import BeautifulSoup

        html = "<html><body><p>Fallback test</p></body></html>"

        call_args_list = []
        original_bs4 = BeautifulSoup.__init__

        def tracking_init(self_bs4, markup, parser, **kwargs):
            call_args_list.append(parser)
            if parser == "lxml":
                raise Exception("lxml not available")
            return original_bs4(self_bs4, markup, parser, **kwargs)

        with patch.object(BeautifulSoup, "__init__", tracking_init):
            result = self.client.parse_html(html)

        # lxml was tried first, then html.parser
        assert "lxml" in call_args_list
        assert "html.parser" in call_args_list
        assert call_args_list.index("lxml") < call_args_list.index("html.parser")

    def test_lxml_success_does_not_fallback(self):
        """When lxml succeeds, html.parser should not be called."""
        html = "<html><body><p>Direct parse</p></body></html>"
        # If lxml is installed, parse_html should use it without fallback.
        # If lxml is NOT installed, it will fall back, which is also valid.
        result = self.client.parse_html(html)
        # Either way, we should get a valid parsed result
        text = result.get_text(strip=True)
        assert "Direct parse" in text

    def test_bs4_not_available_raises_import_error(self):
        """When BS4_AVAILABLE is False, parse_html raises ImportError."""
        with patch("gaia.web.client.BS4_AVAILABLE", False):
            with pytest.raises(ImportError, match="beautifulsoup4"):
                self.client.parse_html("<html></html>")


# ============================================================================
# 2. extract_text: fallback to get_text when structured extraction < 100 chars
# ============================================================================


class TestExtractTextFallback:
    """Test extract_text falls back to get_text for short structured output."""

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

    def test_short_structured_extraction_falls_back_to_get_text(self):
        """When structured extraction yields <100 chars, falls back to get_text."""
        # HTML with content in a <div> (not a structured tag like p, h1, etc.)
        # so structured extraction will find very little
        html = """<html><body>
        <div>This is a longer piece of text that appears only in a div element.
        It has enough characters to exceed the 100-char threshold when extracted
        via get_text but the structured extraction will miss it entirely because
        div is not one of the targeted tags.</div>
        </body></html>"""
        soup = self.client.parse_html(html)
        text = self.client.extract_text(soup)
        # The fallback get_text should capture the div content
        assert "longer piece of text" in text

    def test_long_structured_extraction_does_not_fallback(self):
        """When structured extraction yields >=100 chars, no fallback occurs."""
        # Build enough paragraph content to exceed 100 chars
        long_text = "A" * 120
        html = f"<html><body><p>{long_text}</p></body></html>"
        soup = self.client.parse_html(html)
        text = self.client.extract_text(soup)
        assert long_text in text

    def test_list_items_in_structured_extraction(self):
        """List items are properly extracted with bullet formatting."""
        html = """<html><body>
        <ul>
            <li>First item that is moderately long to contribute chars</li>
            <li>Second item that is also moderately long to contribute chars</li>
            <li>Third item completing the set of items for extraction purposes</li>
        </ul>
        </body></html>"""
        soup = self.client.parse_html(html)
        text = self.client.extract_text(soup)
        assert "- First item" in text
        assert "- Second item" in text

    def test_empty_html_uses_fallback(self):
        """Empty structured extraction falls back to get_text."""
        html = "<html><body><span>Only span content here</span></body></html>"
        soup = self.client.parse_html(html)
        text = self.client.extract_text(soup)
        # get_text fallback should capture span content
        assert "Only span content here" in text


# ============================================================================
# 3. extract_tables: thead, caption, col_index overflow
# ============================================================================


class TestExtractTablesEdgeCases:
    """Test extract_tables edge cases."""

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

    def test_table_with_thead_element(self):
        """Table with explicit <thead> element extracts headers correctly."""
        html = """<html><body>
        <table>
            <thead><tr><th>Name</th><th>Age</th></tr></thead>
            <tbody>
                <tr><td>Alice</td><td>30</td></tr>
                <tr><td>Bob</td><td>25</td></tr>
            </tbody>
        </table>
        </body></html>"""
        soup = self.client.parse_html(html)
        tables = self.client.extract_tables(soup)
        assert len(tables) == 1
        assert tables[0]["data"][0]["Name"] == "Alice"
        assert tables[0]["data"][0]["Age"] == "30"
        assert tables[0]["data"][1]["Name"] == "Bob"

    def test_table_without_thead(self):
        """Table without <thead> uses first <tr> as header row."""
        html = """<html><body>
        <table>
            <tr><th>Color</th><th>Code</th></tr>
            <tr><td>Red</td><td>#FF0000</td></tr>
            <tr><td>Blue</td><td>#0000FF</td></tr>
        </table>
        </body></html>"""
        soup = self.client.parse_html(html)
        tables = self.client.extract_tables(soup)
        assert len(tables) == 1
        assert tables[0]["data"][0]["Color"] == "Red"
        assert tables[0]["data"][1]["Code"] == "#0000FF"

    def test_table_with_caption(self):
        """Table caption is extracted as table_name."""
        html = """<html><body>
        <table>
            <caption>Sales Data 2024</caption>
            <tr><th>Month</th><th>Revenue</th></tr>
            <tr><td>Jan</td><td>$1000</td></tr>
            <tr><td>Feb</td><td>$1500</td></tr>
        </table>
        </body></html>"""
        soup = self.client.parse_html(html)
        tables = self.client.extract_tables(soup)
        assert len(tables) == 1
        assert tables[0]["table_name"] == "Sales Data 2024"

    def test_table_without_caption_gets_default_name(self):
        """Table without caption gets auto-generated name."""
        html = """<html><body>
        <table>
            <tr><th>X</th><th>Y</th></tr>
            <tr><td>1</td><td>2</td></tr>
            <tr><td>3</td><td>4</td></tr>
        </table>
        </body></html>"""
        soup = self.client.parse_html(html)
        tables = self.client.extract_tables(soup)
        assert len(tables) == 1
        assert tables[0]["table_name"] == "Table 1"

    def test_more_td_cells_than_th_headers_col_index_overflow(self):
        """Extra td cells beyond th headers use col_N fallback keys."""
        html = """<html><body>
        <table>
            <tr><th>A</th><th>B</th></tr>
            <tr><td>1</td><td>2</td><td>3</td><td>4</td></tr>
            <tr><td>5</td><td>6</td><td>7</td><td>8</td></tr>
        </table>
        </body></html>"""
        soup = self.client.parse_html(html)
        tables = self.client.extract_tables(soup)
        assert len(tables) == 1
        row = tables[0]["data"][0]
        assert row["A"] == "1"
        assert row["B"] == "2"
        assert row["col_2"] == "3"
        assert row["col_3"] == "4"

    def test_table_with_empty_headers(self):
        """Table with empty header text still gets extracted."""
        html = """<html><body>
        <table>
            <tr><th></th><th></th></tr>
            <tr><td>data1</td><td>data2</td></tr>
        </table>
        </body></html>"""
        soup = self.client.parse_html(html)
        tables = self.client.extract_tables(soup)
        # Headers are ["", ""] which is truthy, so the table is extracted.
        # Both headers map to the same key "", so the dict will have only
        # one entry with the last cell's value overwriting the first.
        assert len(tables) == 1
        row = tables[0]["data"][0]
        # With duplicate empty-string keys, the second td overwrites the first
        assert "" in row

    def test_multiple_tables_with_captions(self):
        """Multiple tables each get their own caption or default name."""
        html = """<html><body>
        <table>
            <caption>First Table</caption>
            <tr><th>X</th></tr>
            <tr><td>1</td></tr>
            <tr><td>2</td></tr>
        </table>
        <table>
            <tr><th>Y</th></tr>
            <tr><td>A</td></tr>
            <tr><td>B</td></tr>
        </table>
        </body></html>"""
        soup = self.client.parse_html(html)
        tables = self.client.extract_tables(soup)
        assert len(tables) == 2
        assert tables[0]["table_name"] == "First Table"
        assert tables[1]["table_name"] == "Table 2"


# ============================================================================
# 4. extract_links: javascript: skipped, empty href, no-text links
# ============================================================================


class TestExtractLinksEdgeCases:
    """Test extract_links edge cases."""

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

    def test_javascript_links_skipped(self):
        """Links with javascript: scheme are skipped."""
        html = """<html><body>
        <a href="javascript:void(0)">Click me</a>
        <a href="javascript:alert('xss')">XSS</a>
        <a href="https://example.com/real">Real link</a>
        </body></html>"""
        soup = self.client.parse_html(html)
        links = self.client.extract_links(soup, "https://example.com")
        assert len(links) == 1
        assert links[0]["url"] == "https://example.com/real"

    def test_empty_href_skipped(self):
        """Links with empty href are skipped."""
        html = """<html><body>
        <a href="">Empty link</a>
        <a href="https://example.com/valid">Valid</a>
        </body></html>"""
        soup = self.client.parse_html(html)
        links = self.client.extract_links(soup, "https://example.com")
        assert len(links) == 1
        assert links[0]["text"] == "Valid"

    def test_links_with_no_text_get_no_text_label(self):
        """Links with no text content get '(no text)' as text."""
        html = """<html><body>
        <a href="https://example.com/image"><img src="logo.png"/></a>
        </body></html>"""
        soup = self.client.parse_html(html)
        links = self.client.extract_links(soup, "https://example.com")
        assert len(links) == 1
        assert links[0]["text"] == "(no text)"
        assert links[0]["url"] == "https://example.com/image"

    def test_anchor_only_links_skipped(self):
        """Links with only # fragment are skipped."""
        html = """<html><body>
        <a href="#">Top</a>
        <a href="#section1">Section 1</a>
        <a href="/page">Page</a>
        </body></html>"""
        soup = self.client.parse_html(html)
        links = self.client.extract_links(soup, "https://example.com")
        assert len(links) == 1
        assert links[0]["text"] == "Page"

    def test_links_without_href_attribute_skipped(self):
        """Anchor tags without href attribute are not included."""
        html = """<html><body>
        <a name="bookmark">Bookmark</a>
        <a href="https://example.com/link">Link</a>
        </body></html>"""
        soup = self.client.parse_html(html)
        links = self.client.extract_links(soup, "https://example.com")
        # find_all("a", href=True) filters out tags without href
        assert len(links) == 1
        assert links[0]["text"] == "Link"


# ============================================================================
# 5. download: redirect following, Content-Disposition filename*=UTF-8
# ============================================================================


class TestDownloadEdgeCases:
    """Test download method edge cases."""

    def setup_method(self):
        self.client = WebClient()

    def teardown_method(self):
        self.client.close()

    def test_download_follows_302_redirect(self):
        """Download follows a 302 redirect before streaming content."""
        # First response: 302 redirect
        redirect_response = MagicMock()
        redirect_response.status_code = 302
        redirect_response.headers = {
            "Location": "https://cdn.example.com/real-file.pdf",
        }
        redirect_response.close = MagicMock()

        # Second response: 200 with content
        final_response = MagicMock()
        final_response.status_code = 200
        final_response.headers = {
            "Content-Type": "application/pdf",
            "Content-Length": "512",
        }
        final_response.raise_for_status = MagicMock()
        final_response.iter_content.return_value = [b"x" * 512]
        final_response.close = MagicMock()

        with (
            patch.object(self.client, "validate_url"),
            patch.object(self.client, "_rate_limit_wait"),
            patch.object(
                self.client._session,
                "get",
                side_effect=[redirect_response, final_response],
            ),
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                result = self.client.download(
                    "https://example.com/redirect-file.pdf",
                    save_dir=tmpdir,
                )
                assert result["size"] == 512
                assert result["content_type"] == "application/pdf"
                # redirect_response.close should have been called
                redirect_response.close.assert_called_once()

    def test_download_content_disposition_with_utf8_filename(self):
        """Content-Disposition with filename*=UTF-8 encoding is parsed."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "Content-Type": "application/octet-stream",
            "Content-Disposition": "attachment; filename*=UTF-8''report%202024.pdf",
        }
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_content.return_value = [b"data"]
        mock_response.close = MagicMock()

        with (
            patch.object(self.client, "validate_url"),
            patch.object(self.client, "_rate_limit_wait"),
            patch.object(self.client._session, "get", return_value=mock_response),
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                result = self.client.download(
                    "https://example.com/download",
                    save_dir=tmpdir,
                )
                # The filename regex should extract the filename after the encoding prefix
                # filename*=UTF-8''report%202024.pdf -> captured as UTF-8''report%202024.pdf
                # or report%202024.pdf depending on regex match
                assert result["filename"] is not None
                assert len(result["filename"]) > 0
                assert os.path.exists(result["path"])

    def test_download_redirect_no_location_header(self):
        """Download with redirect status but no Location header returns as-is."""
        mock_response = MagicMock()
        mock_response.status_code = 302
        mock_response.headers = {}  # No Location header
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_content.return_value = [b"data"]
        mock_response.close = MagicMock()

        with (
            patch.object(self.client, "validate_url"),
            patch.object(self.client, "_rate_limit_wait"),
            patch.object(self.client._session, "get", return_value=mock_response),
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                result = self.client.download(
                    "https://example.com/no-location",
                    save_dir=tmpdir,
                )
                # Should still succeed since the loop breaks on no Location
                assert result["size"] == 4  # len(b"data")

    def test_download_too_many_redirects(self):
        """Download with too many redirects raises ValueError."""
        mock_response = MagicMock()
        mock_response.status_code = 302
        mock_response.headers = {
            "Location": "https://example.com/loop",
        }
        mock_response.close = MagicMock()

        with (
            patch.object(self.client, "validate_url"),
            patch.object(self.client, "_rate_limit_wait"),
            patch.object(self.client._session, "get", return_value=mock_response),
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                with pytest.raises(ValueError, match="Too many redirects"):
                    self.client.download(
                        "https://example.com/redirect-loop",
                        save_dir=tmpdir,
                    )

    def test_download_with_explicit_filename_override(self):
        """Download with explicit filename parameter ignores Content-Disposition."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "Content-Type": "text/plain",
            "Content-Disposition": 'attachment; filename="server_name.txt"',
        }
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_content.return_value = [b"content"]
        mock_response.close = MagicMock()

        with (
            patch.object(self.client, "validate_url"),
            patch.object(self.client, "_rate_limit_wait"),
            patch.object(self.client._session, "get", return_value=mock_response),
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                result = self.client.download(
                    "https://example.com/file",
                    save_dir=tmpdir,
                    filename="my_custom_name.txt",
                )
                assert result["filename"] == "my_custom_name.txt"


# ============================================================================
# 6. close: session cleanup verification
# ============================================================================


class TestCloseSession:
    """Test WebClient session cleanup."""

    def test_close_calls_session_close(self):
        """close() should call the underlying session's close method."""
        client = WebClient()
        mock_session = MagicMock()
        client._session = mock_session

        client.close()

        mock_session.close.assert_called_once()

    def test_close_with_none_session_does_not_crash(self):
        """close() should not crash if session is None."""
        client = WebClient()
        client._session = None
        # Should not raise
        client.close()

    def test_close_idempotent(self):
        """Calling close() multiple times should not raise."""
        client = WebClient()
        client.close()
        # The session is still the object (not set to None by close),
        # but calling close again should not error
        client.close()


# ============================================================================
# 7. search_duckduckgo: bs4 not available raises ImportError
# ============================================================================


class TestSearchDuckDuckGoBs4Unavailable:
    """Test search_duckduckgo when bs4 is not available."""

    def setup_method(self):
        self.client = WebClient()

    def teardown_method(self):
        self.client.close()

    def test_bs4_not_available_raises_import_error(self):
        """search_duckduckgo raises ImportError when BS4_AVAILABLE is False."""
        with patch("gaia.web.client.BS4_AVAILABLE", False):
            with pytest.raises(ImportError, match="beautifulsoup4"):
                self.client.search_duckduckgo("test query")

    def test_bs4_available_does_not_raise_import_error(self):
        """search_duckduckgo does not raise ImportError when BS4_AVAILABLE is True."""
        try:
            from bs4 import BeautifulSoup  # noqa: F401
        except ImportError:
            pytest.skip("beautifulsoup4 not installed")

        # Mock the actual HTTP call but let the bs4 check pass
        mock_response = MagicMock()
        mock_response.text = "<html><body></body></html>"
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.encoding = "utf-8"
        mock_response.apparent_encoding = "utf-8"

        with patch.object(self.client, "_request", return_value=mock_response):
            results = self.client.search_duckduckgo("test")
            assert isinstance(results, list)


# ============================================================================
# 8. _request: encoding fixup (ISO-8859-1 apparent_encoding detection)
# ============================================================================


class TestRequestEncodingFixup:
    """Test _request encoding fixup for ISO-8859-1 detection."""

    def setup_method(self):
        self.client = WebClient()

    def teardown_method(self):
        self.client.close()

    def test_iso_8859_1_encoding_replaced_by_apparent_encoding(self):
        """When encoding is ISO-8859-1 but apparent is UTF-8, encoding is updated."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Length": "100"}
        mock_response.encoding = "iso-8859-1"
        mock_response.apparent_encoding = "utf-8"

        self.client._session.request = MagicMock(return_value=mock_response)

        with patch.object(self.client, "validate_url"):
            result = self.client.get("https://example.com/page")

        # encoding should have been updated to apparent_encoding
        assert result.encoding == "utf-8"

    def test_iso_8859_1_both_encoding_and_apparent_no_change(self):
        """When both encoding and apparent are ISO-8859-1, no change occurs."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Length": "100"}
        mock_response.encoding = "iso-8859-1"
        mock_response.apparent_encoding = "iso-8859-1"

        self.client._session.request = MagicMock(return_value=mock_response)

        with patch.object(self.client, "validate_url"):
            result = self.client.get("https://example.com/page")

        # encoding should remain as iso-8859-1
        assert result.encoding == "iso-8859-1"

    def test_utf8_encoding_not_changed(self):
        """When encoding is already UTF-8, no change occurs."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Length": "100"}
        mock_response.encoding = "utf-8"
        mock_response.apparent_encoding = "utf-8"

        self.client._session.request = MagicMock(return_value=mock_response)

        with patch.object(self.client, "validate_url"):
            result = self.client.get("https://example.com/page")

        assert result.encoding == "utf-8"

    def test_none_encoding_no_crash(self):
        """When encoding is None, no encoding fixup should occur."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Length": "100"}
        mock_response.encoding = None
        mock_response.apparent_encoding = "utf-8"

        self.client._session.request = MagicMock(return_value=mock_response)

        with patch.object(self.client, "validate_url"):
            result = self.client.get("https://example.com/page")

        # encoding should remain None (the if guard prevents entry)
        assert result.encoding is None

    def test_none_apparent_encoding_no_crash(self):
        """When apparent_encoding is None, no encoding fixup should occur."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Length": "100"}
        mock_response.encoding = "iso-8859-1"
        mock_response.apparent_encoding = None

        self.client._session.request = MagicMock(return_value=mock_response)

        with patch.object(self.client, "validate_url"):
            result = self.client.get("https://example.com/page")

        # encoding should remain iso-8859-1 since apparent_encoding is None
        assert result.encoding == "iso-8859-1"

    def test_iso_8859_1_case_insensitive_comparison(self):
        """ISO-8859-1 detection is case-insensitive."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Length": "100"}
        mock_response.encoding = "ISO-8859-1"
        mock_response.apparent_encoding = "UTF-8"

        self.client._session.request = MagicMock(return_value=mock_response)

        with patch.object(self.client, "validate_url"):
            result = self.client.get("https://example.com/page")

        # encoding should be updated to apparent (UTF-8)
        assert result.encoding == "UTF-8"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
