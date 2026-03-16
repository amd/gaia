# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Web search and webpage reading tools for GAIA agents.

Provides two tools:
- web_search: Search the web via Perplexity API (direct HTTP call)
- read_webpage: Fetch a URL and extract clean content (text, links, full)

Both tools are registered via WebSearchMixin.register_web_search_tools(),
following the same pattern as BrowserToolsMixin.

Usage:
    class MyAgent(Agent, WebSearchMixin):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._web_client = WebClient()

        def _register_tools(self):
            self.register_web_search_tools()
"""

import json
import logging
import os
from typing import Any, Dict

import requests

logger = logging.getLogger(__name__)

# Maximum content size to return to LLM (prevents context window overflow)
MAX_CONTENT_LENGTH = 50000  # ~50k chars, reasonable for most LLM contexts

# Perplexity API endpoint
PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"
PERPLEXITY_MODEL = "sonar"  # Perplexity's search-augmented model


def _call_perplexity_api(query: str) -> Dict[str, Any]:
    """Call Perplexity API directly via HTTP.

    This is a standalone function (not a method) so it can be easily mocked
    in tests and reused outside the mixin.

    Args:
        query: Search query string

    Returns:
        Dict with keys: success, answer, sources, error (if failed)
    """
    api_key = os.environ.get("PERPLEXITY_API_KEY", "")
    if not api_key:
        logger.warning("PERPLEXITY_API_KEY not set — web search unavailable")
        return {
            "success": False,
            "error": "PERPLEXITY_API_KEY not set. Set this environment variable to enable web search.",
            "answer": "",
            "sources": [],
        }

    try:
        logger.info("Calling Perplexity API for query: %s", query[:80])

        response = requests.post(
            PERPLEXITY_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": PERPLEXITY_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a helpful research assistant. Provide concise, "
                            "factual answers with sources when available."
                        ),
                    },
                    {"role": "user", "content": query},
                ],
            },
            timeout=30,
        )

        if response.status_code == 401:
            logger.error("Perplexity API: Invalid API key (401)")
            return {
                "success": False,
                "error": "Invalid PERPLEXITY_API_KEY. Check your API key.",
                "answer": "",
                "sources": [],
            }

        if response.status_code == 429:
            logger.warning("Perplexity API: Rate limited (429)")
            return {
                "success": False,
                "error": "Perplexity API rate limit exceeded. Try again later.",
                "answer": "",
                "sources": [],
            }

        if response.status_code != 200:
            logger.error(
                "Perplexity API error: HTTP %d — %s",
                response.status_code,
                response.text[:200],
            )
            return {
                "success": False,
                "error": f"Perplexity API returned HTTP {response.status_code}",
                "answer": "",
                "sources": [],
            }

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("Perplexity API returned malformed JSON: %s", e)
            return {
                "success": False,
                "error": f"Perplexity API returned malformed response: {e}",
                "answer": "",
                "sources": [],
            }

        # Extract answer from OpenAI-compatible response format
        answer = ""
        choices = data.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            answer = message.get("content", "")

        # Extract citations if present (Perplexity-specific field)
        sources = data.get("citations", [])

        # Treat empty answer as a failure — the API call succeeded but
        # returned nothing useful, which is misleading if we say success=True.
        if not answer or not answer.strip():
            logger.warning("Perplexity API returned 200 but empty answer")
            return {
                "success": False,
                "error": "Perplexity returned no answer for this query. Try rephrasing.",
                "answer": "",
                "sources": sources,
            }

        logger.info(
            "Perplexity search successful: %d chars answer, %d sources",
            len(answer),
            len(sources),
        )

        return {
            "success": True,
            "answer": answer,
            "sources": sources,
        }

    except requests.exceptions.Timeout:
        logger.error("Perplexity API request timed out")
        return {
            "success": False,
            "error": "Perplexity API request timed out. Try again later.",
            "answer": "",
            "sources": [],
        }
    except requests.exceptions.ConnectionError as e:
        logger.error("Perplexity API connection error: %s", e)
        return {
            "success": False,
            "error": f"Perplexity service unavailable: {e}",
            "answer": "",
            "sources": [],
        }
    except Exception as e:
        logger.error("Perplexity API unexpected error: %s", e, exc_info=True)
        return {
            "success": False,
            "error": f"Web search failed: {e}",
            "answer": "",
            "sources": [],
        }


class WebSearchMixin:
    """Web search and webpage reading tools for any GAIA agent.

    Provides two atomic tools:
    - web_search(query) — search the web via Perplexity API
    - read_webpage(url, extract) — fetch a URL and extract content

    Tool registration follows GAIA pattern: register_web_search_tools() method.

    The mixin expects self._web_client to be set to a WebClient instance
    before read_webpage is used. If not set, read_webpage returns an error.

    Usage:
        class MyAgent(Agent, WebSearchMixin):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                from gaia.web.client import WebClient
                self._web_client = WebClient()

            def _register_tools(self):
                self.register_web_search_tools()
    """

    _web_client = None  # WebClient instance, set by agent init

    def register_web_search_tools(self) -> None:
        """Register web_search and read_webpage tools."""
        from gaia.agents.base.tools import tool

        mixin = self  # Capture self for nested functions

        def _ensure_web_client() -> bool:
            """Check that web client is available."""
            return mixin._web_client is not None

        # ================================================================
        # web_search tool
        # ================================================================

        @tool(atomic=True)
        def web_search(query: str) -> Dict:
            """Search the web for current information using Perplexity AI.

            Use this tool to find current information, trends, best practices,
            API documentation, error solutions, or any web-accessible knowledge.

            Args:
                query: Search query string (e.g., "Gmail API setup OAuth2 Python",
                       "latest AI trends 2026", "how to fix CORS error in FastAPI")

            Returns:
                Dictionary with:
                - success: Whether the search was successful
                - answer: Concise answer text from Perplexity
                - sources: List of source URLs used to generate the answer
                - error: Error message if search failed

            Example:
                result = web_search("Python FastAPI authentication best practices")
                if result["success"]:
                    print(result["answer"])
                    for src in result["sources"]:
                        print(f"  Source: {src}")
            """
            try:
                logger.info("web_search called with query: %s", query[:100])
                result = _call_perplexity_api(query)
                return result
            except Exception as e:
                logger.error("web_search unexpected error: %s", e, exc_info=True)
                return {
                    "success": False,
                    "error": f"Web search failed unexpectedly: {e}",
                    "answer": "",
                    "sources": [],
                }

        # ================================================================
        # read_webpage tool
        # ================================================================

        @tool(atomic=True)
        def read_webpage(url: str, extract: str = "text") -> Dict:
            """Fetch a URL and extract clean content for LLM reasoning.

            Retrieves the page at the given URL and returns readable content.
            Use after web_search to read full articles, documentation, or any URL.

            Args:
                url: Full URL to fetch (must start with http:// or https://)
                extract: What to extract:
                    - "text": Main content only (strips nav, ads, footers)
                    - "links": All links on the page (text + URL pairs)
                    - "full": Everything (includes nav, headers, footers)

            Returns:
                Dictionary with:
                - success: Whether the fetch was successful
                - url: The fetched URL
                - title: Page title (for HTML pages)
                - content: Extracted text content (for text/full modes)
                - links: List of {text, url} dicts (for links mode)
                - content_type: HTTP content type
                - truncated: Whether content was truncated
                - error: Error message if fetch failed

            Example:
                result = read_webpage("https://docs.python.org/3/tutorial/", extract="text")
                if result["success"]:
                    print(result["title"])
                    print(result["content"])
            """
            # Validate extract mode
            valid_modes = {"text", "links", "full"}
            if extract not in valid_modes:
                return _make_error_result(
                    url,
                    f"Invalid extract mode '{extract}'. "
                    f"Must be one of: {', '.join(sorted(valid_modes))}",
                )

            if not _ensure_web_client():
                return _make_error_result(
                    url, "Web client not initialized. Cannot fetch URLs."
                )

            try:
                logger.info(
                    "read_webpage called: url=%s, extract=%s", url[:100], extract
                )

                # Fetch the page
                response = mixin._web_client.get(url)
                response.raise_for_status()

                content_type = response.headers.get("Content-Type", "")

                # ---- Non-HTML content handling ----
                if (
                    "text/html" not in content_type
                    and "application/xhtml" not in content_type
                ):
                    return _handle_non_html(url, response, content_type)

                # ---- HTML content handling ----
                try:
                    soup = mixin._web_client.parse_html(response.text)
                except ImportError as e:
                    return _make_error_result(url, f"HTML parsing unavailable: {e}")

                # Get page title
                title_tag = soup.find("title")
                title = title_tag.get_text(strip=True) if title_tag else "(no title)"

                if extract == "links":
                    return _extract_links(url, title, content_type, soup)
                elif extract == "full":
                    return _extract_full(url, title, content_type, soup)
                else:  # "text" (default)
                    return _extract_text(url, title, content_type, soup)

            except ValueError as e:
                # URL validation errors from WebClient
                logger.warning("read_webpage URL error: %s", e)
                return _make_error_result(url, str(e))
            except requests.exceptions.Timeout as e:
                logger.warning("read_webpage timeout: %s", e)
                return _make_error_result(url, f"Request timed out: {e}")
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else "unknown"
                logger.warning("read_webpage HTTP error %s: %s", status, e)
                return _make_error_result(url, f"HTTP error {status}: {e}")
            except Exception as e:
                logger.error("read_webpage error: %s", e, exc_info=True)
                return _make_error_result(url, f"Failed to fetch page: {e}")

        # ================================================================
        # Internal helpers (closures that capture mixin)
        # ================================================================

        def _truncate_content(text: str, max_length: int = MAX_CONTENT_LENGTH) -> tuple:
            """Truncate text to max_length, return (text, was_truncated)."""
            if len(text) <= max_length:
                return text, False
            # Truncate at word boundary
            truncated = text[:max_length]
            last_space = truncated.rfind(" ")
            if last_space > max_length * 0.8:
                truncated = truncated[:last_space]
            truncated += "\n\n... (content truncated)"
            return truncated, True

        def _make_result(
            url: str,
            title: str = "",
            content: str = "",
            links: list = None,
            content_type: str = "",
            truncated: bool = False,
        ) -> Dict[str, Any]:
            """Build a consistent success result dict.

            All success results include both 'content' and 'links' keys
            so consumers can safely access either without KeyError.
            """
            return {
                "success": True,
                "url": url,
                "title": title,
                "content": content,
                "links": links or [],
                "content_type": content_type,
                "truncated": truncated,
            }

        def _make_error_result(url: str, error: str) -> Dict[str, Any]:
            """Build a consistent error result dict.

            Mirrors _make_result so every key present in success results
            is also present in error results — prevents KeyError in consumers.
            """
            return {
                "success": False,
                "url": url,
                "title": "",
                "content": "",
                "links": [],
                "content_type": "",
                "truncated": False,
                "error": error,
            }

        def _handle_non_html(url: str, response, content_type: str) -> Dict[str, Any]:
            """Handle non-HTML responses (JSON, plain text, binary)."""
            # Text-based content — return directly
            text_types = [
                "application/json",
                "text/plain",
                "text/csv",
                "text/xml",
                "application/xml",
            ]
            if any(t in content_type for t in text_types):
                content, truncated = _truncate_content(response.text)
                return _make_result(
                    url=url,
                    title="(non-HTML content)",
                    content=content,
                    content_type=content_type,
                    truncated=truncated,
                )

            # Binary content — return metadata only
            size = response.headers.get("Content-Length", "unknown")
            return _make_result(
                url=url,
                title="(binary content)",
                content=(
                    f"Binary content detected ({content_type}, size: {size} bytes).\n"
                    f"This URL returns non-text content (e.g., PDF, image, binary file).\n"
                    f"Use a download tool to save it locally for analysis."
                ),
                content_type=content_type,
            )

        def _extract_text(
            url: str, title: str, content_type: str, soup
        ) -> Dict[str, Any]:
            """Extract readable text from HTML (strips nav/ads/footers)."""
            # Disable extract_text's internal truncation entirely by passing
            # a very large max_length.  _truncate_content is the sole
            # truncator — this prevents the double-suffix bug where
            # extract_text adds "... (truncated)" and then _truncate_content
            # adds "... (content truncated)" on top.
            text = mixin._web_client.extract_text(
                soup, max_length=MAX_CONTENT_LENGTH * 2
            )
            content, truncated = _truncate_content(text)
            return _make_result(
                url=url,
                title=title,
                content=content,
                content_type=content_type,
                truncated=truncated,
            )

        def _extract_links(
            url: str, title: str, content_type: str, soup
        ) -> Dict[str, Any]:
            """Extract all links from HTML page."""
            links = mixin._web_client.extract_links(soup, url)
            return _make_result(
                url=url,
                title=title,
                links=links,
                content_type=content_type,
            )

        def _extract_full(
            url: str, title: str, content_type: str, soup
        ) -> Dict[str, Any]:
            """Extract full text content (including nav/headers/footers)."""
            full_text = soup.get_text(separator="\n", strip=True)
            content, truncated = _truncate_content(full_text)
            return _make_result(
                url=url,
                title=title,
                content=content,
                content_type=content_type,
                truncated=truncated,
            )
