# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Keenable MCP connector (#7852): opt-in, keyless, pinned, search tier 2.

Tavily stays the tier-1 search connector; Keenable sits next to Brave Search.
"""

from __future__ import annotations

import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
from gaia.agents.tools.browser_tools import _parse_search_text
from gaia.connectors.catalog.mcp_servers import (
    _ALL_SPECS,
    _BRAVE_SEARCH,
    _KEENABLE,
    _TAVILY,
)
from gaia.connectors.registry import REGISTRY
from gaia.connectors.spec import WEB_SEARCH

from .test_catalog_pins import package_spec_problem


def test_keenable_is_registered():
    assert _KEENABLE in _ALL_SPECS
    assert REGISTRY.get("mcp-keenable") is _KEENABLE


def test_keenable_is_an_opt_in_search_connector():
    assert _KEENABLE.category == "search"
    assert _KEENABLE.tier == _BRAVE_SEARCH.tier == 2
    assert _TAVILY.tier == 1
    assert _KEENABLE.provides == (WEB_SEARCH,)
    assert _KEENABLE.web_search_tool == "search_web_pages"


def test_keenable_needs_no_key():
    assert _KEENABLE.mcp_env_keys == ()
    assert _KEENABLE.config_schema == ()


def test_keenable_package_is_pinned():
    assert _KEENABLE.mcp_command == "npx"
    assert _KEENABLE.mcp_args == ("-y", "@keenable/mcp@0.1.1")
    assert package_spec_problem(_KEENABLE.mcp_command, _KEENABLE.mcp_args) is None


def test_keenable_text_results_parse():
    # The plain-text shape search_web_pages returns.
    text = (
        "Title: GAIA on Ryzen AI\n"
        "URL: https://example.com/gaia\n"
        "Published: 2025-03-20\n"
        "Snippets:\n"
        "Run local LLMs.\n"
        "\n"
        "Title: Lemonade Server\n"
        "URL: https://example.com/lemonade\n"
    )
    results = _parse_search_text(text)
    assert [r["url"] for r in results] == [
        "https://example.com/gaia",
        "https://example.com/lemonade",
    ]
    assert results[0]["title"] == "GAIA on Ryzen AI"
