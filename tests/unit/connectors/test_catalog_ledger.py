# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Catalog ledger test (#976, updated #1021).

Replaces the previous "legacy ⊆ new" parity check. The MCP catalog has been
intentionally reduced from 22 deployed entries down to 3 carried-over entries
(#1021 removed mcp-filesystem and mcp-fetch because no built-in agent consumes
them through the connectors framework; custom agents supply their own
mcp_servers.json instead), plus mcp-tavily, mcp-slack (#1002), and mcp-discord
(#1003) added net-new afterwards. This test asserts both ends of that ledger:

  * KEPT_IDS — exactly these ids must remain in
    ``connectors.catalog.mcp_servers`` (the 3 carried over from the legacy
    catalog, plus the net-new ids).
  * DELETED_IDS — these 16 ids must NOT be present. Regression guard against
    accidentally re-introducing untested catalog tiles.
"""

from __future__ import annotations

from gaia.connectors.registry import REGISTRY

KEPT_IDS = frozenset(
    {
        "mcp-github",
        "mcp-memory",
        "mcp-git",
        # Net-new (not part of the original 22): a real tavily-mcp@latest
        # server. The same keyring TAVILY_API_KEY is also read by the
        # gaia.web.tavily Python wrapper.
        "mcp-tavily",
        # Re-added in #1002 on the maintained slack-mcp-server, pinned and
        # covered by test_slack_mcp_connector.py.
        "mcp-slack",
        # Added in #1003: pinned mcp-discord, covered by
        # test_discord_mcp_connector.py.
        "mcp-discord",
        # Launch-checked by tests/integration/test_mcp_catalog_launch.py.
        "mcp-notion",
        "mcp-brave-search",
    }
)

DELETED_IDS = frozenset(
    {
        # Removed in #1021: no built-in agent consumes these via the connectors
        # framework; the File/Web agents use Python-native tool mixins instead.
        # Custom agents supply their own mcp_servers.json.
        "mcp-filesystem",
        "mcp-fetch",
        # Removed in #976: untested / redundant entries.
        "mcp-playwright",
        "mcp-desktop-commander",
        "mcp-postgres",
        "mcp-context7",
        "mcp-gmail",
        "mcp-google-calendar",
        "mcp-outlook",
        "mcp-spotify",
        "mcp-linear",
        "mcp-jira",
        "mcp-stripe",
        "mcp-sendgrid",
        "mcp-windows-automation",
        "mcp-microsoft-learn",
    }
)


def _mcp_specs():
    """All registered specs of type=mcp_server."""
    # Importing the catalog module triggers REGISTRY.register() on each spec.
    import gaia.connectors.catalog.mcp_servers  # noqa: F401

    return {s.id: s for s in REGISTRY.all() if s.type == "mcp_server"}


def test_kept_ids_present_in_catalog():
    """Every id in KEPT_IDS exists in the registry as type=mcp_server."""
    actual = _mcp_specs()
    missing = KEPT_IDS - actual.keys()
    assert not missing, f"Kept ids missing from catalog: {sorted(missing)}"


def test_deleted_ids_absent_from_catalog():
    """No id in DELETED_IDS may reappear in the registry."""
    actual = _mcp_specs()
    surfaced = DELETED_IDS & actual.keys()
    assert not surfaced, (
        f"Untested catalog ids were re-added: {sorted(surfaced)}. "
        "If you want to ship one of these again, add explicit test coverage "
        "first and remove the id from DELETED_IDS in this file."
    )


def test_no_unexpected_mcp_ids():
    """The catalog contains only kept ids — no surprise additions."""
    actual = _mcp_specs()
    extra = actual.keys() - KEPT_IDS
    assert not extra, (
        f"Unexpected MCP catalog ids beyond the kept set: {sorted(extra)}. "
        "Add the id to KEPT_IDS (with rationale) or remove it from the catalog."
    )


def test_mcp_id_naming_convention():
    """Every type=mcp_server id matches the deployed prefix convention."""
    for spec_id in _mcp_specs():
        assert spec_id.startswith("mcp-"), (
            f"Connector id {spec_id!r} violates the mcp- prefix convention "
            "for type=mcp_server entries."
        )
