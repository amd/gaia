# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Keenable MCP connector (#7852): opt-in, keyless, pinned, search tier 2.

Tavily stays the tier-1 search connector; Keenable sits next to Brave Search.
"""

from __future__ import annotations

import pytest

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
    assert results[0]["snippet"] == "Run local LLMs."


def test_cloud_search_connectors_send_data_offsite():
    assert _TAVILY.sends_data_offsite
    assert _BRAVE_SEARCH.sends_data_offsite
    assert _KEENABLE.sends_data_offsite


def test_local_only_refuses_keenable(tmp_path, monkeypatch):
    """A user who chose Local only cannot turn Keenable on or search with it."""
    from gaia.agents.tools.browser_tools import _connector_fallback
    from gaia.connectors.api import activate
    from gaia.connectors.errors import ConfigurationError
    from gaia.connectors.offsite import refuse_offsite_connector
    from gaia.llm import local_only

    monkeypatch.setenv("GAIA_POLICY_FILE", str(tmp_path / "policy.json"))
    local_only.set_policy("local_ai_only")

    with pytest.raises(ConfigurationError, match="Local only is on"):
        refuse_offsite_connector(_KEENABLE)
    with pytest.raises(ConfigurationError, match="Keenable"):
        activate("mcp-keenable", "agent-1", scopes_for_grant=["use"])

    monkeypatch.setattr(
        "gaia.connectors.mcp_server.is_mcp_server_configured",
        lambda connector_id: connector_id == "mcp-keenable",
    )
    monkeypatch.setattr(
        "gaia.connectors.mcp_server.is_mcp_server_enabled",
        lambda connector_id: connector_id == "mcp-keenable",
    )

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("Keenable search ran while Local only is on")

    monkeypatch.setattr(
        "gaia.agents.tools.browser_tools._mcp_connector_search", should_not_run
    )
    monkeypatch.setattr(
        "gaia.agents.tools.browser_tools._tavily_search", should_not_run
    )
    results, name, errors = _connector_fallback(object(), "local models", 5)
    assert results is None
    assert name is None
    assert any("Keenable" in error and "Local only" in error for error in errors)
