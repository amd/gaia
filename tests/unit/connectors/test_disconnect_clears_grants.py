# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Per-agent grants must be wiped on connector disconnect (#976 security fix).

Before this fix, ``McpServerHandler.disconnect`` and
``OAuthPkceHandler.disconnect`` cleared keyring + state but never touched
``grants.json``. Re-adding a connector with the same id later would silently
re-attach the previous user's agent consents — a real bypass.

This test exercises the cycle: configure → grant → disconnect → check that
``list_agent_grants`` for that connector_id is empty. Both handler types
share the ``revoke_all_grants_for`` helper.
"""

from __future__ import annotations
from typing import Any, Dict
from unittest.mock import patch

import pytest

from gaia.connectors.grants import (
    grant_agent,
    list_agent_grants,
    revoke_all_grants_for,
)


@pytest.fixture
def grants_in_tmp(tmp_path, monkeypatch):
    """Redirect grants.json to a tempdir for this test."""
    grants_dir = tmp_path / ".gaia" / "connectors"
    grants_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    # Re-evaluate _grants_path under the new HOME.
    monkeypatch.setattr(
        "gaia.connectors.grants._grants_path",
        lambda: grants_dir / "grants.json",
    )
    yield grants_dir


def test_revoke_all_grants_for_clears_every_agent(grants_in_tmp):
    grant_agent("mcp-test", "agent-A", ["use"])
    grant_agent("mcp-test", "agent-B", ["use"])
    grant_agent("mcp-other", "agent-A", ["use"])

    revoked = revoke_all_grants_for("mcp-test")
    assert sorted(revoked) == ["agent-A", "agent-B"]

    # mcp-test grants are gone.
    assert list_agent_grants("mcp-test") == {}
    # mcp-other is unaffected.
    assert list_agent_grants("mcp-other") == {"agent-A": ["use"]}


def test_revoke_all_grants_for_unknown_id_is_noop(grants_in_tmp):
    grant_agent("mcp-test", "agent-A", ["use"])

    revoked = revoke_all_grants_for("nonexistent")
    assert revoked == []
    # mcp-test untouched.
    assert list_agent_grants("mcp-test") == {"agent-A": ["use"]}


@pytest.mark.asyncio
async def test_mcp_server_disconnect_revokes_grants(grants_in_tmp, tmp_path):
    """
    End-to-end: configure → grant → disconnect → assert grants empty.
    Re-configure with the same id and assert grants are still empty
    (no silent inheritance).
    """
    from gaia.connectors.mcp_server import McpServerHandler
    from gaia.connectors.spec import ConfigField, ConnectorSpec

    spec = ConnectorSpec(
        id="mcp-disconnect-test",
        display_name="Disconnect Test",
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

    # Redirect ~/.gaia for the mcp_server module too.
    monkey_home = tmp_path
    with patch("gaia.connectors.mcp_server._mcp_servers_path") as mock_path:
        mock_path.return_value = monkey_home / ".gaia" / "mcp_servers.json"

        handler = McpServerHandler()

        await handler.configure(spec, {"API_KEY": "secret-1"})

        # Grant two agents.
        grant_agent(spec.id, "agent-A", ["use"])
        grant_agent(spec.id, "agent-B", ["use"])
        assert sorted(list_agent_grants(spec.id).keys()) == ["agent-A", "agent-B"]

        # Disconnect must wipe both grants.
        await handler.disconnect(spec)
        assert list_agent_grants(spec.id) == {}

        # Re-configure with the same id (different secret value).
        await handler.configure(spec, {"API_KEY": "secret-2"})
        # Grants stay empty — no silent inheritance from the previous lifecycle.
        assert list_agent_grants(spec.id) == {}, (
            "Re-configuring a connector with the same id silently inherited "
            "the previous user's agent grants — security bypass."
        )


@pytest.mark.asyncio
async def test_oauth_pkce_disconnect_revokes_grants(grants_in_tmp, monkeypatch):
    """OAuth-pkce disconnect must also wipe grants for the connector_id."""
    from gaia.connectors.oauth_pkce import OAuthPkceHandler

    captured: Dict[str, Any] = {}

    async def _no_op_get_credential(*args, **kwargs):  # pragma: no cover
        return {}

    # delete_connection in oauth_pkce talks to the keyring; stub it out.
    monkeypatch.setattr(
        "gaia.connectors.oauth_pkce.delete_connection",
        lambda provider_id, account_email: captured.update(
            provider_id=provider_id, account_email=account_email
        ),
    )

    from gaia.connectors.spec import ConnectorSpec

    spec = ConnectorSpec(
        id="oauth-test",
        display_name="OAuth Test",
        icon="🧪",
        category="test",
        tier=1,
        type="oauth_pkce",
        description="ephemeral",
        oauth_provider_ref="oauth-test",
    )

    grant_agent(spec.id, "agent-A", ["scope.read"])
    grant_agent(spec.id, "agent-B", ["scope.read"])
    assert sorted(list_agent_grants(spec.id).keys()) == ["agent-A", "agent-B"]

    handler = OAuthPkceHandler()
    await handler.disconnect(spec)

    assert list_agent_grants(spec.id) == {}
    assert captured == {"provider_id": "oauth-test", "account_email": "default"}
