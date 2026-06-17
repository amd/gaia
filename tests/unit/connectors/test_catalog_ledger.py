# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Catalog ledger test (#976, updated #1021).

Replaces the previous "legacy ⊆ new" parity check. The MCP catalog has been
intentionally reduced from 22 deployed entries down to 3 carried-over entries
(#1021 removed mcp-filesystem and mcp-fetch because no built-in agent consumes
them through the connectors framework; custom agents supply their own
mcp_servers.json instead), plus mcp-tavily added net-new afterwards. This test
asserts both ends of that ledger:

  * KEPT_IDS — exactly these ids must remain in
    ``connectors.catalog.mcp_servers`` (the 3 carried over from the legacy
    catalog, plus mcp-tavily). For each carried-over id, field-by-field
    equivalence against the legacy ``mcp.py:_CATALOG`` row of the same name is
    asserted as a guard against silent drift during the migration.
  * DELETED_IDS — these 19 ids must NOT be present. Regression guard against
    accidentally re-introducing untested catalog tiles.

When ``src/gaia/ui/routers/mcp.py:_CATALOG`` is finally deleted (also part of
this PR), the field-equivalence half of this test stops running (the legacy
fixture is gone) but the kept-set/deleted-set membership half stays as the
permanent regression guard.
"""

from __future__ import annotations

import pytest

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
        "mcp-brave-search",
        "mcp-postgres",
        "mcp-context7",
        "mcp-gmail",
        "mcp-google-calendar",
        "mcp-outlook",
        "mcp-spotify",
        "mcp-slack",
        "mcp-notion",
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


def _legacy_catalog_by_name():
    """
    Legacy ``_CATALOG`` from ``src/gaia/ui/routers/mcp.py`` keyed by name.
    Returns an empty dict once the legacy catalog is deleted from the source —
    in that case the field-equivalence assertions are skipped (the kept-set /
    deleted-set membership assertions still run).
    """
    try:
        from gaia.ui.routers.mcp import _CATALOG  # type: ignore[attr-defined]
    except ImportError:
        return {}
    return {entry["name"]: entry for entry in _CATALOG}


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


@pytest.mark.parametrize("spec_id", sorted(KEPT_IDS))
def test_kept_spec_matches_legacy_fields(spec_id):
    """
    Field-by-field equivalence against legacy ``_CATALOG``.
    Skips automatically once the legacy catalog is deleted (post-#976 migration
    completes). This is a one-shot guard for the migration commit.
    """
    legacy_by_name = _legacy_catalog_by_name()
    if not legacy_by_name:
        pytest.skip("legacy _CATALOG already removed; field-equivalence step done")

    legacy_name = spec_id[len("mcp-") :]  # "mcp-github" → "github"
    legacy = legacy_by_name.get(legacy_name)
    assert legacy is not None, (
        f"Kept id {spec_id!r} has no legacy counterpart with name={legacy_name!r}; "
        "either the kept set is wrong or the legacy catalog drifted."
    )

    spec = _mcp_specs()[spec_id]

    assert spec.display_name == legacy["display_name"], (
        f"display_name drift for {spec_id}: "
        f"new={spec.display_name!r} legacy={legacy['display_name']!r}"
    )
    assert spec.mcp_command == legacy["command"], (
        f"command drift for {spec_id}: "
        f"new={spec.mcp_command!r} legacy={legacy['command']!r}"
    )
    assert tuple(spec.mcp_args) == tuple(legacy["args"]), (
        f"args drift for {spec_id}: "
        f"new={tuple(spec.mcp_args)!r} legacy={tuple(legacy['args'])!r}"
    )
    assert set(spec.mcp_env_keys) == set(legacy["env"].keys()), (
        f"env keys drift for {spec_id}: "
        f"new={set(spec.mcp_env_keys)} legacy={set(legacy['env'].keys())}"
    )

    expected_config_keys = set(legacy.get("requires_config") or [])
    actual_config_keys = {field.key for field in spec.config_schema}
    assert actual_config_keys == expected_config_keys, (
        f"config_schema key set drift for {spec_id}: "
        f"new={actual_config_keys} legacy={expected_config_keys}"
    )
