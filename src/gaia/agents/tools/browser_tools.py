# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Browser Tools for web content extraction and search.

Provides lightweight web browsing tools using requests + BeautifulSoup
(no Playwright or browser binaries). Enables agents to fetch web pages,
search the web, and download files for local analysis.
"""

import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class BrowserToolsMixin:
    """Web browsing tools for content extraction, search, and download.

    Gives the agent the ability to fetch web pages, extract structured data,
    search the web, and download files — all without a browser engine.

    Tool registration follows GAIA pattern: register_browser_tools() method.

    The mixin expects self._web_client to be set to a WebClient instance
    before tools are used. If not set, tools return helpful error messages.
    """

    _web_client = None  # WebClient instance, set by agent init

    def register_browser_tools(self) -> None:
        """Register browser tools for web content extraction."""
        from gaia.agents.base.tools import tool

        mixin = self  # Capture self for nested functions

        def _ensure_web_client() -> bool:
            """Check that web client is available."""
            if mixin._web_client is None:
                return False
            return True

        @tool(atomic=True)
        def fetch_page(
            url: str,
            extract: str = "text",
            max_length: int = 5000,
        ) -> str:
            """Fetch a web page and extract its content.

            Retrieves the page at the given URL and returns readable text content.
            Use this to read articles, documentation, reference pages, or any web content.
            Does NOT execute JavaScript — works best with static content, articles, docs.

            Args:
                url: The full URL to fetch (must start with http:// or https://)
                extract: What to extract - 'text' (readable content), 'html' (raw HTML),
                         'links' (all links on page), 'tables' (HTML tables as JSON)
                max_length: Maximum characters to return (default: 5000, max: 20000)
            """
            if not _ensure_web_client():
                return "Error: Browser tools not initialized. Web browsing is disabled."

            # Clamp max_length to prevent extreme values
            max_length = max(100, min(max_length, 20000))

            # Validate extract mode
            valid_modes = {"text", "html", "links", "tables"}
            if extract not in valid_modes:
                return (
                    f"Error: Invalid extract mode '{extract}'. "
                    f"Must be one of: {', '.join(sorted(valid_modes))}"
                )

            try:
                response = mixin._web_client.get(url)
                response.raise_for_status()
            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                logger.error(f"Error fetching {url}: {e}")
                return f"Error fetching page: {e}"

            content_type = response.headers.get("Content-Type", "")

            # If it's not HTML, return raw text or suggest download
            if (
                "text/html" not in content_type
                and "application/xhtml" not in content_type
            ):
                if any(
                    t in content_type
                    for t in ["application/json", "text/plain", "text/csv", "text/xml"]
                ):
                    # Text-based content — return directly
                    text = response.text[:max_length]
                    if len(response.text) > max_length:
                        text += "\n\n... (truncated)"
                    return (
                        f"Content from: {url}\n"
                        f"Type: {content_type}\n"
                        f"Length: {len(response.text):,} chars\n\n"
                        f"{text}"
                    )
                else:
                    # Binary content — suggest download
                    size = response.headers.get("Content-Length", "unknown")
                    return (
                        f"This URL returns binary content ({content_type}, size: {size}).\n"
                        f"Use download_file to save it locally for analysis."
                    )

            # Parse HTML
            try:
                soup = mixin._web_client.parse_html(response.text)
            except ImportError as e:
                return f"Error: {e}"

            # Get page title
            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else "(no title)"

            if extract == "html":
                html = response.text[:max_length]
                if len(response.text) > max_length:
                    html += "\n\n... (truncated)"
                return (
                    f"Page: {title}\n"
                    f"URL: {url}\n"
                    f"Length: {len(response.text):,} chars\n\n"
                    f"{html}"
                )

            elif extract == "links":
                links = mixin._web_client.extract_links(soup, url)
                if not links:
                    return f"Page: {title}\nURL: {url}\n\nNo links found on this page."

                lines = [f"Page: {title}", f"URL: {url}", f"Links: {len(links)}", ""]
                for i, link in enumerate(links[:100], 1):  # Cap at 100 links
                    lines.append(f"  {i}. {link['text']}")
                    lines.append(f"     {link['url']}")

                if len(links) > 100:
                    lines.append(f"\n... and {len(links) - 100} more links")

                result = "\n".join(lines)
                if len(result) > max_length:
                    result = result[:max_length] + "\n\n... (truncated)"
                return result

            elif extract == "tables":
                tables = mixin._web_client.extract_tables(soup)
                if not tables:
                    return f"Page: {title}\nURL: {url}\n\nNo data tables found on this page."

                lines = [
                    f"Page: {title}",
                    f"URL: {url}",
                    f"Tables found: {len(tables)}",
                    "",
                ]
                for table in tables:
                    lines.append(f"--- {table['table_name']} ---")
                    # Format as JSON for easy insert_data consumption
                    table_json = json.dumps(table["data"], indent=2)
                    lines.append(table_json)
                    lines.append("")

                result = "\n".join(lines)
                if len(result) > max_length:
                    result = result[:max_length] + "\n\n... (truncated)"
                return result

            else:  # text (default)
                text = mixin._web_client.extract_text(soup, max_length=max_length)
                return (
                    f"Page: {title}\n"
                    f"URL: {url}\n"
                    f"Length: {len(text):,} chars\n\n"
                    f"{text}"
                )

        @tool(atomic=True)
        def search_web(
            query: str,
            num_results: int = 5,
        ) -> str:
            """Search the web and return results with titles, URLs, and snippets.

            Uses DuckDuckGo to find relevant web pages. Returns titles, URLs, and
            brief descriptions. Use fetch_page to read the full content of any result.

            Args:
                query: Search query string
                num_results: Number of results to return (default: 5, max: 10)
            """
            if not _ensure_web_client():
                return "Error: Browser tools not initialized. Web search is disabled."

            # Clamp num_results
            num_results = max(1, min(num_results, 10))

            try:
                results = mixin._web_client.search_duckduckgo(
                    query, num_results=num_results
                )
            except ImportError as e:
                return f"Error: {e}"
            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                logger.error(f"Error searching web: {e}")
                return (
                    f"Error performing web search: {e}\n"
                    "Try using fetch_page with a direct URL instead."
                )

            if not results:
                return (
                    f'No results found for: "{query}"\n\n'
                    "Try different search terms or use fetch_page with a direct URL."
                )

            lines = [f'Web search results for: "{query}"', ""]
            for i, result in enumerate(results, 1):
                lines.append(f"{i}. {result['title']}")
                lines.append(f"   {result['url']}")
                if result.get("snippet"):
                    lines.append(f"   {result['snippet']}")
                lines.append("")

            lines.append("Use fetch_page(url) to read the full content of any result.")
            return "\n".join(lines)

        @tool(atomic=True)
        def download_file(
            url: str,
            save_to: str = "~/Downloads",
            filename: str = None,
        ) -> str:
            """Download a file from a URL to the local filesystem.

            Downloads the file and saves it locally. Useful for getting documents,
            PDFs, CSVs, images, or any file from the web for local analysis.
            After downloading, use read_file or index_document to process it.

            Args:
                url: Direct URL to the file to download
                save_to: Local directory to save the file (default: ~/Downloads)
                filename: Override filename (default: derived from URL or Content-Disposition)
            """
            if not _ensure_web_client():
                return "Error: Browser tools not initialized. Download is disabled."

            # Validate save path with PathValidator if available
            if hasattr(mixin, "_path_validator") and mixin._path_validator:
                from pathlib import Path

                resolved_dir = str(Path(save_to).expanduser().resolve())
                if not mixin._path_validator.is_path_allowed(
                    resolved_dir, prompt_user=True
                ):
                    return f"Error: Access denied to directory: {save_to}"

            try:
                result = mixin._web_client.download(
                    url=url,
                    save_dir=save_to,
                    filename=filename,
                )
            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                logger.error(f"Error downloading {url}: {e}")
                return f"Error downloading file: {e}"

            # Format file size
            size_bytes = result["size"]
            if size_bytes >= 1024 * 1024:
                size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
            elif size_bytes >= 1024:
                size_str = f"{size_bytes / 1024:.1f} KB"
            else:
                size_str = f"{size_bytes} bytes"

            return (
                f"Downloaded: {result['filename']}\n"
                f"  Saved to: {result['path']}\n"
                f"  Size: {size_str}\n"
                f"  Type: {result['content_type']}\n\n"
                f"Use read_file or index_document to process this file."
            )
