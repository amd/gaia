# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Per-MCP enable/disable toggle (#1004).

The ``disabled`` flag is already persisted per-entry in ``mcp_servers.json``
(written today by ``McpServerHandler.configure``) and ``MCPClientManager``
already skips disabled entries — so the runtime suppression path is in
place. What's missing is an API to flip the flag without re-running the
full ``configure`` round-trip (which would re-prompt for credentials).

These tests cover ``McpServerHandler.set_enabled`` and
``set_reload_callback`` — the two new methods this PR adds.
"""

from __future__ import annotations

import os
import stat
from typing import Dict
from unittest.mock import patch

import pytest

from gaia.connectors.errors import ConnectorsError
from gaia.connectors.mcp_server import (
    McpServerHandler,
    _read_mcp_servers_json,
)
from gaia.connectors.spec import ConfigField, ConnectorSpec
from gaia.connectors.store import SERVICE_NAME

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """Redirect ``~/.gaia/mcp_servers.json`` to a tempdir."""
    monkeypatch.setattr("gaia.connectors.mcp_server.Path.home", lambda: tmp_path)
    return tmp_path


def _make_mcp_spec(
    *,
    spec_id: str = "mcp-github",
    mcp_env_keys: tuple = ("GITHUB_TOKEN",),
) -> ConnectorSpec:
    return ConnectorSpec(
        id=spec_id,
        display_name="GitHub MCP",
        icon="🐙",
        category="dev-tools",
        tier=1,
        type="mcp_server",
        description="GitHub MCP server",
        mcp_command="npx",
        mcp_args=("-y", "@modelcontextprotocol/server-github"),
        mcp_env_keys=mcp_env_keys,
        config_schema=tuple(
            ConfigField(key=k, label=k, kind="secret", secret=True)
            for k in mcp_env_keys
        ),
    )


def _make_oauth_spec() -> ConnectorSpec:
    return ConnectorSpec(
        id="google",
        display_name="Google",
        icon="🔵",
        category="cloud",
        tier=1,
        type="oauth_pkce",
        description="Google OAuth",
        oauth_provider_ref="google",
    )


@pytest.fixture
async def configured_handler(tmp_path):
    """Build a handler with one configured MCP entry and a mock reload callback."""
    spec = _make_mcp_spec()
    reload_calls: list[int] = []
    handler = McpServerHandler(reload_callback=lambda: reload_calls.append(1))

    # Pre-configure the entry. We patch keyring.set_password so the test
    # doesn't touch the real OS keyring.
    stored: Dict[tuple, str] = {}

    def fake_set_password(service, username, value):
        stored[(service, username)] = value

    with patch(
        "gaia.connectors.mcp_server.keyring.set_password",
        side_effect=fake_set_password,
    ):
        await handler.configure(spec, {"GITHUB_TOKEN": "ghp_secret"})

    # Drain the reload calls from configure() — tests should observe their own.
    reload_calls.clear()
    return handler, spec, reload_calls, stored


# ---------------------------------------------------------------------------
# A1.1 — set_enabled flips the disabled flag without touching anything else
# ---------------------------------------------------------------------------


class TestSetEnabledPersistence:
    @pytest.mark.asyncio
    async def test_disable_flips_flag_only(self, configured_handler, tmp_path):
        handler, spec, _, _ = configured_handler

        # Before disable: disabled flag is False (configure() default).
        before = _read_mcp_servers_json()[spec.id]
        assert before["disabled"] is False
        before_env = dict(before["env"])
        before_command = before["command"]
        before_args = list(before["args"])

        await handler.set_enabled(spec.id, False)

        after = _read_mcp_servers_json()[spec.id]
        assert after["disabled"] is True
        # Env block (with $keyring refs), command, args all unchanged.
        assert after["env"] == before_env
        assert after["command"] == before_command
        assert list(after["args"]) == before_args

    @pytest.mark.asyncio
    async def test_enable_flips_flag_back(self, configured_handler):
        handler, spec, _, _ = configured_handler

        await handler.set_enabled(spec.id, False)
        await handler.set_enabled(spec.id, True)

        after = _read_mcp_servers_json()[spec.id]
        assert after["disabled"] is False

    @pytest.mark.asyncio
    async def test_round_trip_preserves_keyring_refs(self, configured_handler):
        """Disable → enable → disable must not mutate the env block."""
        handler, spec, _, _ = configured_handler

        original_env = dict(_read_mcp_servers_json()[spec.id]["env"])

        await handler.set_enabled(spec.id, False)
        await handler.set_enabled(spec.id, True)
        await handler.set_enabled(spec.id, False)

        final_env = dict(_read_mcp_servers_json()[spec.id]["env"])
        assert final_env == original_env
        # Specifically, the $keyring reference is preserved (no plaintext leak).
        assert final_env["GITHUB_TOKEN"] == {
            "$keyring": f"{SERVICE_NAME}:mcp-github:GITHUB_TOKEN"
        }

    @pytest.mark.asyncio
    async def test_idempotent_re_disable_is_harmless(self, configured_handler):
        handler, spec, _, _ = configured_handler

        await handler.set_enabled(spec.id, False)
        # Second disable on already-disabled entry is a no-op (or harmless write).
        await handler.set_enabled(spec.id, False)

        assert _read_mcp_servers_json()[spec.id]["disabled"] is True


# ---------------------------------------------------------------------------
# A1.2 — File mode 0600 (mirror #976 hardening)
# ---------------------------------------------------------------------------


class TestFilePermissions:
    @pytest.mark.asyncio
    async def test_file_mode_0600_after_disable(self, configured_handler, tmp_path):
        handler, spec, _, _ = configured_handler

        await handler.set_enabled(spec.id, False)

        path = tmp_path / ".gaia" / "mcp_servers.json"
        st = os.stat(path)
        # On Windows chmod is best-effort; only assert on Unix.
        if hasattr(os, "geteuid"):
            mode = stat.S_IMODE(st.st_mode)
            assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


# ---------------------------------------------------------------------------
# A1.3 — Type guard and unknown-id errors
# ---------------------------------------------------------------------------


class TestErrors:
    @pytest.mark.asyncio
    async def test_unknown_id_raises_actionable_error(self, configured_handler):
        handler, _, _, _ = configured_handler

        with pytest.raises(ConnectorsError, match="not configured"):
            await handler.set_enabled("mcp-nonexistent", False)

    @pytest.mark.asyncio
    async def test_unknown_id_error_names_the_id(self, configured_handler):
        handler, _, _, _ = configured_handler

        with pytest.raises(ConnectorsError, match="mcp-totally-fake"):
            await handler.set_enabled("mcp-totally-fake", False)

    @pytest.mark.asyncio
    async def test_unknown_id_does_not_corrupt_file(self, configured_handler):
        handler, spec, _, _ = configured_handler

        before = _read_mcp_servers_json()
        try:
            await handler.set_enabled("mcp-bogus", False)
        except ConnectorsError:
            pass
        after = _read_mcp_servers_json()
        # The good entry is still present and unchanged.
        assert after == before


# ---------------------------------------------------------------------------
# A1.4 — Reload callback wiring
# ---------------------------------------------------------------------------


class TestReloadCallback:
    @pytest.mark.asyncio
    async def test_reload_fires_once_per_call(self, configured_handler):
        handler, spec, reload_calls, _ = configured_handler

        await handler.set_enabled(spec.id, False)
        assert len(reload_calls) == 1

        await handler.set_enabled(spec.id, True)
        assert len(reload_calls) == 2

    @pytest.mark.asyncio
    async def test_reload_not_called_when_unset(self, tmp_path):
        """A handler without a reload callback still flips the flag and warns."""
        spec = _make_mcp_spec()
        handler = McpServerHandler()  # no callback

        with patch("gaia.connectors.mcp_server.keyring.set_password"):
            await handler.configure(spec, {"GITHUB_TOKEN": "tok"})

        # No callback means no exception when disabling — just a warning.
        await handler.set_enabled(spec.id, False)

        assert _read_mcp_servers_json()[spec.id]["disabled"] is True

    @pytest.mark.asyncio
    async def test_set_reload_callback_attaches_after_construction(self, tmp_path):
        """The setter lets the FastAPI lifespan wire the callback post-import."""
        spec = _make_mcp_spec()
        handler = McpServerHandler()  # no callback at construction
        reload_calls: list[int] = []

        # Public setter (added in this PR) lets the lifespan attach a callback
        # after both the handler singleton and the manager are initialized.
        handler.set_reload_callback(lambda: reload_calls.append(1))

        with patch("gaia.connectors.mcp_server.keyring.set_password"):
            await handler.configure(spec, {"GITHUB_TOKEN": "tok"})

        await handler.set_enabled(spec.id, False)

        # configure() triggered one reload, set_enabled() triggered another.
        assert len(reload_calls) == 2


# ---------------------------------------------------------------------------
# A1.5 — Sanity check that grants.json is untouched by toggle
# ---------------------------------------------------------------------------


class TestGrantsUnchanged:
    @pytest.mark.asyncio
    async def test_disable_does_not_revoke_grants(self, configured_handler, tmp_path):
        """Toggling disabled state must NOT call revoke_all_grants_for —
        that's disconnect's job (per #976), and inheriting our own grants
        on re-enable is the whole point of the feature."""
        handler, spec, _, _ = configured_handler

        # ``revoke_all_grants_for`` is imported lazily inside ``disconnect()``
        # to avoid a circular import — patch it at the source module.
        with patch("gaia.connectors.grants.revoke_all_grants_for") as mock_revoke:
            await handler.set_enabled(spec.id, False)
            await handler.set_enabled(spec.id, True)

            assert mock_revoke.call_count == 0, "set_enabled must not revoke grants"
