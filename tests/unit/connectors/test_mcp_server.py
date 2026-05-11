# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-5 unit tests — McpServerHandler + $keyring scheme + MCP catalog.

Tests cover:
- McpServerHandler.configure stores env vars in keyring
- McpServerHandler.configure writes mcp_servers.json with $keyring refs
- McpServerHandler.configure calls reload callback
- McpServerHandler.configure raises ConnectorsError on missing env keys
- McpServerHandler.get_credential resolves env vars from keyring
- McpServerHandler.get_credential fails closed on missing keyring entry
- McpServerHandler.disconnect removes entry from mcp_servers.json
- McpServerHandler.disconnect deletes keyring entries
- McpServerHandler.test returns ok=True when all keys present
- McpServerHandler.test returns ok=False with detail on missing keys
- mcp_servers.json has no plaintext secrets after configure
- MCPClient._resolve_keyring_refs resolves references
- MCPClient._resolve_keyring_refs fails closed on missing entry
- MCPClientManager.reload() disconnects, reloads, reconnects
- MCP catalog: all 22 specs registered, type="mcp_server"
"""

from __future__ import annotations

import json
from typing import Dict
from unittest.mock import patch

import pytest

from gaia.connectors.errors import ConnectorsError
from gaia.connectors.handler import _HANDLER_REGISTRY
from gaia.connectors.mcp_server import (
    McpServerHandler,
    _read_mcp_servers_json,
    _write_mcp_servers_json,
)
from gaia.connectors.spec import ConnectorSpec
from gaia.connectors.store import SERVICE_NAME
from gaia.mcp.client.mcp_client import _resolve_keyring_refs

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr("gaia.connectors.mcp_server.Path.home", lambda: tmp_path)
    return tmp_path


def _make_spec(
    *,
    id: str = "mcp-github",
    mcp_command: str = "npx",
    mcp_args: tuple = ("-y", "@modelcontextprotocol/server-github"),
    mcp_env_keys: tuple = ("GITHUB_TOKEN",),
) -> ConnectorSpec:
    return ConnectorSpec(
        id=id,
        display_name="GitHub MCP",
        icon="🐙",
        category="dev-tools",
        tier=1,
        type="mcp_server",
        description="GitHub MCP server",
        mcp_command=mcp_command,
        mcp_args=mcp_args,
        mcp_env_keys=mcp_env_keys,
    )


# ---------------------------------------------------------------------------
# McpServerHandler.configure
# ---------------------------------------------------------------------------


class TestConfigure:
    @pytest.mark.asyncio
    async def test_stores_env_vars_in_keyring(self, tmp_path):
        spec = _make_spec()
        handler = McpServerHandler()
        stored: Dict[str, str] = {}

        def fake_set_password(service, username, value):
            stored[(service, username)] = value

        with (
            patch(
                "gaia.connectors.mcp_server.keyring.set_password",
                side_effect=fake_set_password,
            ),
            patch("gaia.connectors.mcp_server.keyring.get_password", return_value=None),
        ):
            await handler.configure(spec, {"GITHUB_TOKEN": "ghp_secret"})

        assert stored[(SERVICE_NAME, "mcp-github:GITHUB_TOKEN")] == "ghp_secret"

    @pytest.mark.asyncio
    async def test_writes_keyring_refs_not_plaintext(self, tmp_path):
        spec = _make_spec()
        handler = McpServerHandler()

        with patch("gaia.connectors.mcp_server.keyring.set_password"):
            await handler.configure(spec, {"GITHUB_TOKEN": "ghp_secret"})

        path = tmp_path / ".gaia" / "mcp_servers.json"
        assert path.exists()
        content = path.read_text()
        # Secret must NOT appear in file
        assert "ghp_secret" not in content
        # $keyring reference must appear
        assert "$keyring" in content

    @pytest.mark.asyncio
    async def test_keyring_ref_format(self, tmp_path):
        spec = _make_spec()
        handler = McpServerHandler()

        with patch("gaia.connectors.mcp_server.keyring.set_password"):
            await handler.configure(spec, {"GITHUB_TOKEN": "tok"})

        path = tmp_path / ".gaia" / "mcp_servers.json"
        data = json.loads(path.read_text())
        env_block = data["mcpServers"]["mcp-github"]["env"]
        assert env_block["GITHUB_TOKEN"] == {
            "$keyring": f"{SERVICE_NAME}:mcp-github:GITHUB_TOKEN"
        }

    @pytest.mark.asyncio
    async def test_calls_reload_callback(self):
        spec = _make_spec()
        reload_calls = []
        handler = McpServerHandler(reload_callback=lambda: reload_calls.append(1))

        with patch("gaia.connectors.mcp_server.keyring.set_password"):
            await handler.configure(spec, {"GITHUB_TOKEN": "tok"})

        assert len(reload_calls) == 1

    @pytest.mark.asyncio
    async def test_missing_required_env_key_raises(self):
        spec = _make_spec(mcp_env_keys=("GITHUB_TOKEN", "GITHUB_ORG"))
        handler = McpServerHandler()

        with pytest.raises(ConnectorsError, match="missing required env keys"):
            await handler.configure(spec, {"GITHUB_TOKEN": "tok"})  # missing GITHUB_ORG

    @pytest.mark.asyncio
    async def test_no_env_keys_spec_configures_ok(self):
        spec = _make_spec(mcp_env_keys=())
        handler = McpServerHandler()

        result = await handler.configure(spec, {})
        assert result["configured"] is True


# ---------------------------------------------------------------------------
# McpServerHandler.get_credential
# ---------------------------------------------------------------------------


class TestGetCredential:
    @pytest.mark.asyncio
    async def test_resolves_env_from_keyring(self):
        spec = _make_spec()
        handler = McpServerHandler()

        with patch(
            "gaia.connectors.mcp_server.keyring.get_password",
            return_value="ghp_resolved",
        ):
            result = await handler.get_credential(spec)

        assert result["env"]["GITHUB_TOKEN"] == "ghp_resolved"
        assert result["command"] == "npx"
        assert result["args"] == ["-y", "@modelcontextprotocol/server-github"]

    @pytest.mark.asyncio
    async def test_fails_closed_on_missing_keyring_entry(self):
        spec = _make_spec()
        handler = McpServerHandler()

        with patch(
            "gaia.connectors.mcp_server.keyring.get_password",
            return_value=None,
        ):
            with pytest.raises(ConnectorsError, match="missing keyring entries"):
                await handler.get_credential(spec)

    @pytest.mark.asyncio
    async def test_no_env_keys_returns_empty_env(self):
        spec = _make_spec(mcp_env_keys=())
        handler = McpServerHandler()
        result = await handler.get_credential(spec)
        assert result["env"] == {}


# ---------------------------------------------------------------------------
# McpServerHandler.disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_removes_entry_from_mcp_servers_json(self, tmp_path):
        spec = _make_spec()
        handler = McpServerHandler()

        # Pre-populate mcp_servers.json
        _write_mcp_servers_json({"mcp-github": {"command": "npx"}})

        with patch("gaia.connectors.mcp_server.keyring.delete_password"):
            await handler.disconnect(spec)

        servers = _read_mcp_servers_json()
        assert "mcp-github" not in servers

    @pytest.mark.asyncio
    async def test_deletes_keyring_entries(self):
        spec = _make_spec()
        handler = McpServerHandler()
        deleted = []

        def fake_delete(service, username):
            deleted.append((service, username))

        with patch(
            "gaia.connectors.mcp_server.keyring.delete_password",
            side_effect=fake_delete,
        ):
            await handler.disconnect(spec)

        assert (SERVICE_NAME, "mcp-github:GITHUB_TOKEN") in deleted

    @pytest.mark.asyncio
    async def test_idempotent_when_not_configured(self):
        spec = _make_spec()
        handler = McpServerHandler()

        import keyring.errors

        with patch(
            "gaia.connectors.mcp_server.keyring.delete_password",
            side_effect=keyring.errors.PasswordDeleteError("not found"),
        ):
            await handler.disconnect(spec)  # must not raise

    @pytest.mark.asyncio
    async def test_calls_reload_callback(self):
        spec = _make_spec()
        reload_calls = []
        handler = McpServerHandler(reload_callback=lambda: reload_calls.append(1))

        with patch("gaia.connectors.mcp_server.keyring.delete_password"):
            await handler.disconnect(spec)

        assert len(reload_calls) == 1


# ---------------------------------------------------------------------------
# McpServerHandler.test
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_ok_when_all_keys_present(self):
        spec = _make_spec()
        handler = McpServerHandler()

        with patch(
            "gaia.connectors.mcp_server.keyring.get_password",
            return_value="some-value",
        ):
            result = await handler.test(spec)

        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_fail_when_key_missing(self):
        spec = _make_spec()
        handler = McpServerHandler()

        with patch(
            "gaia.connectors.mcp_server.keyring.get_password",
            return_value=None,
        ):
            result = await handler.test(spec)

        assert result["ok"] is False
        assert "GITHUB_TOKEN" in result["detail"]

    @pytest.mark.asyncio
    async def test_ok_when_no_keys_required(self):
        spec = _make_spec(mcp_env_keys=())
        handler = McpServerHandler()
        result = await handler.test(spec)
        assert result["ok"] is True
        assert result["detail"] == "no_secrets_required"


# ---------------------------------------------------------------------------
# $keyring resolution in MCPClient
# ---------------------------------------------------------------------------


class TestResolveKeyringRefs:
    def test_resolves_reference(self):
        with patch("keyring.get_password", return_value="resolved"):
            result = _resolve_keyring_refs(
                {"KEY": {"$keyring": "gaia.connections:mcp-test:KEY"}}
            )
        assert result["KEY"] == "resolved"

    def test_passes_through_plain_string(self):
        result = _resolve_keyring_refs({"KEY": "plain"})
        assert result["KEY"] == "plain"

    def test_fails_closed_on_missing_entry(self):
        from gaia.connectors.errors import ConnectorsError

        with patch("keyring.get_password", return_value=None):
            with pytest.raises(ConnectorsError, match="missing keyring entries"):
                _resolve_keyring_refs(
                    {"KEY": {"$keyring": "gaia.connections:mcp-test:KEY"}}
                )

    def test_refuses_foreign_service(self):
        """A $keyring ref outside ``gaia.connections`` is refused — never
        forwarded to the keyring, preventing cross-service exfiltration via
        a corrupted ``mcp_servers.json``."""
        from gaia.connectors.errors import ConnectorsError

        called = {"n": 0}

        def fake_get(service, username):
            called["n"] += 1
            return "leaked-secret"

        with patch("keyring.get_password", side_effect=fake_get):
            with pytest.raises(ConnectorsError, match="outside the gaia namespace"):
                _resolve_keyring_refs(
                    {"KEY": {"$keyring": "Chrome Safe Storage:Chrome:K"}}
                )
        assert (
            called["n"] == 0
        ), "keyring.get_password must NOT be called for foreign services"

    def test_empty_env_returns_empty_dict(self):
        assert _resolve_keyring_refs({}) == {}
        assert _resolve_keyring_refs(None) == {}

    def test_resolves_multiple_refs(self):
        def fake_get(service, username):
            return {
                "gaia.connections:mcp-test:K1": "val1",
                "gaia.connections:mcp-test:K2": "val2",
            }.get(f"{service}:{username}")

        with patch("keyring.get_password", side_effect=fake_get):
            result = _resolve_keyring_refs(
                {
                    "K1": {"$keyring": "gaia.connections:mcp-test:K1"},
                    "K2": {"$keyring": "gaia.connections:mcp-test:K2"},
                }
            )
        assert result == {"K1": "val1", "K2": "val2"}


# ---------------------------------------------------------------------------
# MCPClientManager.reload
# ---------------------------------------------------------------------------


class TestMCPClientManagerReload:
    def test_reload_calls_disconnect_all_then_load_from_config(self):
        from gaia.mcp.client.mcp_client_manager import MCPClientManager

        manager = MCPClientManager()
        disconnect_called = []
        load_called = []

        with (
            patch.object(
                manager,
                "disconnect_all",
                side_effect=lambda: disconnect_called.append(1),
            ),
            patch.object(
                manager, "load_from_config", side_effect=lambda: load_called.append(1)
            ),
            patch.object(manager.config, "_load"),
        ):
            manager.reload()

        assert len(disconnect_called) == 1
        assert len(load_called) == 1


# ---------------------------------------------------------------------------
# Secret hygiene: no plaintext secrets in mcp_servers.json
# ---------------------------------------------------------------------------


class TestSecretHygiene:
    @pytest.mark.asyncio
    async def test_no_secret_in_mcp_servers_json(self, tmp_path):
        spec = _make_spec(mcp_env_keys=("GITHUB_TOKEN", "SLACK_TOKEN"))
        spec2 = ConnectorSpec(
            id="mcp-slack",
            display_name="Slack",
            icon="💬",
            category="comm",
            tier=2,
            type="mcp_server",
            description="Slack",
            mcp_command="npx",
            mcp_args=("-y", "slack"),
            mcp_env_keys=("SLACK_TOKEN",),
        )
        handler = McpServerHandler()

        secrets = {
            "GITHUB_TOKEN": "super_secret_github",
            "SLACK_TOKEN": "super_secret_slack",
        }

        with patch("gaia.connectors.mcp_server.keyring.set_password"):
            await handler.configure(spec, secrets)

        path = tmp_path / ".gaia" / "mcp_servers.json"
        content = path.read_text()
        for secret_val in secrets.values():
            assert (
                secret_val not in content
            ), f"Secret '{secret_val}' found in mcp_servers.json"


# ---------------------------------------------------------------------------
# Catalog: 22 MCP server specs registered
# ---------------------------------------------------------------------------


class TestCatalog:
    def test_mcp_catalog_entries_have_mcp_server_type(self):
        # Count assertion lives in test_catalog_ledger.py; this just checks
        # that the catalog still contains ``type="mcp_server"`` entries and
        # that registration fires at import time.
        from gaia.connectors import catalog  # noqa: F401 — triggers registration
        from gaia.connectors.registry import REGISTRY

        mcp_specs = [s for s in REGISTRY.all() if s.type == "mcp_server"]
        assert mcp_specs, "Expected at least one mcp_server spec in the registry"
        for spec in mcp_specs:
            assert spec.type == "mcp_server"

    def test_mcp_server_handler_registered(self):
        import gaia.connectors.mcp_server  # noqa: F401

        assert "mcp_server" in _HANDLER_REGISTRY

    def test_github_mcp_spec_has_env_keys(self):
        import gaia.connectors.catalog.mcp_servers  # noqa: F401
        from gaia.connectors.catalog.mcp_servers import _GITHUB

        assert "GITHUB_TOKEN" in _GITHUB.mcp_env_keys

    def test_no_spec_has_env_keys_without_config_schema(self):
        import gaia.connectors.catalog.mcp_servers as m

        for spec in m._ALL_SPECS:
            if spec.mcp_env_keys:
                assert (
                    spec.config_schema
                ), f"Spec '{spec.id}' has mcp_env_keys but no config_schema"


class TestIsMcpServerConfigured:
    """``is_mcp_server_configured`` is the source-of-truth lookup for
    the catalog UI's "configured" tile state — must reflect whatever is
    in mcp_servers.json without any caching of its own."""

    def test_returns_false_when_file_missing(self, tmp_path, monkeypatch):
        from gaia.connectors.mcp_server import is_mcp_server_configured

        monkeypatch.setattr("gaia.connectors.mcp_server.Path.home", lambda: tmp_path)
        assert is_mcp_server_configured("mcp-github") is False

    def test_returns_true_when_entry_present(self, tmp_path, monkeypatch):
        import json

        from gaia.connectors.mcp_server import is_mcp_server_configured

        monkeypatch.setattr("gaia.connectors.mcp_server.Path.home", lambda: tmp_path)
        path = tmp_path / ".gaia" / "mcp_servers.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"mcpServers": {"mcp-github": {"command": "x"}}}))
        assert is_mcp_server_configured("mcp-github") is True
        assert is_mcp_server_configured("mcp-other") is False

    def test_corrupt_file_raises_connectors_error(self, tmp_path, monkeypatch):
        from gaia.connectors.errors import ConnectorsError
        from gaia.connectors.mcp_server import is_mcp_server_configured

        monkeypatch.setattr("gaia.connectors.mcp_server.Path.home", lambda: tmp_path)
        path = tmp_path / ".gaia" / "mcp_servers.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        with pytest.raises(ConnectorsError):
            is_mcp_server_configured("mcp-github")
