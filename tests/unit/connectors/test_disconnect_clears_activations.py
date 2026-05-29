# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Per-agent activations must be wiped on connector disconnect (issue #1005).

Symmetric to ``test_disconnect_clears_grants.py``: re-adding a connector
with the same id later must NOT silently re-attach the previous user's
tool-visibility decisions either.

The cycle exercised: configure → grant → activate → disconnect → check
that activations for that connector_id are empty. Both handler types
share the ``revoke_all_activations_for`` helper.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import patch

import pytest

from gaia.connectors.activations import (
    activate_agent,
    list_agent_activations,
    revoke_all_activations_for,
)
from gaia.connectors.grants import (
    grant_agent,
    list_agent_grants,
)


@pytest.fixture
def home_in_tmp(tmp_path, monkeypatch):
    """Redirect both grants.json and activations.json to a tempdir."""
    connectors_dir = tmp_path / ".gaia" / "connectors"
    connectors_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "gaia.connectors.grants._grants_path",
        lambda: connectors_dir / "grants.json",
    )
    monkeypatch.setattr(
        "gaia.connectors.activations._activations_path",
        lambda: connectors_dir / "activations.json",
    )
    yield connectors_dir


def test_revoke_all_activations_for_clears_every_agent(home_in_tmp):
    activate_agent("mcp-test", "agent-A")
    activate_agent("mcp-test", "agent-B")
    activate_agent("mcp-other", "agent-A")

    revoked = revoke_all_activations_for("mcp-test")
    assert sorted(revoked) == ["agent-A", "agent-B"]

    assert list_agent_activations("mcp-test") == {}
    # Unrelated connector untouched.
    assert list_agent_activations("mcp-other") == {"agent-A": True}


def test_revoke_all_activations_for_unknown_id_is_noop(home_in_tmp):
    activate_agent("mcp-test", "agent-A")
    revoked = revoke_all_activations_for("nonexistent")
    assert revoked == []
    assert list_agent_activations("mcp-test") == {"agent-A": True}


@pytest.mark.asyncio
async def test_mcp_server_disconnect_clears_activations(home_in_tmp, tmp_path):
    """
    End-to-end: configure → grant → activate → disconnect → assert both
    grants and activations are empty. Re-configure with the same id and
    assert they are still empty (no silent inheritance, either axis).
    """
    from gaia.connectors.mcp_server import McpServerHandler
    from gaia.connectors.spec import ConfigField, ConnectorSpec

    spec = ConnectorSpec(
        id="mcp-disconnect-activation-test",
        display_name="Disconnect Activations Test",
        icon="🧪",
        category="test",
        tier=1,
        type="mcp_server",
        description="ephemeral test entry",
        mcp_command="echo",
        mcp_args=("hello",),
        mcp_env_keys=("API_KEY",),
        config_schema=(
            ConfigField(
                key="API_KEY",
                label="API Key",
                kind="secret",
                secret=True,
            ),
        ),
    )

    with patch("gaia.connectors.mcp_server._mcp_servers_path") as mock_path:
        mock_path.return_value = tmp_path / ".gaia" / "mcp_servers.json"

        handler = McpServerHandler()
        await handler.configure(spec, {"API_KEY": "secret-1"})

        # Grant + activate two agents.
        grant_agent(spec.id, "agent-A", ["use"])
        grant_agent(spec.id, "agent-B", ["use"])
        activate_agent(spec.id, "agent-A")
        activate_agent(spec.id, "agent-B")
        assert sorted(list_agent_activations(spec.id).keys()) == [
            "agent-A",
            "agent-B",
        ]

        # Disconnect must wipe both axes.
        await handler.disconnect(spec)
        assert list_agent_grants(spec.id) == {}
        assert list_agent_activations(spec.id) == {}

        # Re-configure with the same id.
        await handler.configure(spec, {"API_KEY": "secret-2"})
        assert list_agent_grants(spec.id) == {}, (
            "Re-configuring a connector with the same id silently inherited "
            "the previous user's grants — security bypass."
        )
        assert list_agent_activations(spec.id) == {}, (
            "Re-configuring a connector with the same id silently inherited "
            "the previous user's activations — security bypass."
        )


@pytest.mark.asyncio
async def test_oauth_pkce_disconnect_clears_activations(home_in_tmp, monkeypatch):
    """OAuth-pkce disconnect must wipe activations alongside grants."""
    from gaia.connectors.oauth_pkce import OAuthPkceHandler

    captured: Dict[str, Any] = {}

    monkeypatch.setattr(
        "gaia.connectors.oauth_pkce.delete_connection",
        lambda provider_id, account_email: captured.update(
            provider_id=provider_id, account_email=account_email
        ),
    )

    from gaia.connectors.spec import ConnectorSpec

    spec = ConnectorSpec(
        id="oauth-activation-test",
        display_name="OAuth Activation Test",
        icon="🧪",
        category="test",
        tier=1,
        type="oauth_pkce",
        description="ephemeral",
        oauth_provider_ref="oauth-activation-test",
    )

    grant_agent(spec.id, "agent-A", ["scope.read"])
    activate_agent(spec.id, "agent-A")
    activate_agent(spec.id, "agent-B")

    handler = OAuthPkceHandler()
    await handler.disconnect(spec)

    assert list_agent_grants(spec.id) == {}
    assert list_agent_activations(spec.id) == {}
    assert captured == {
        "provider_id": "oauth-activation-test",
        "account_email": "default",
    }
