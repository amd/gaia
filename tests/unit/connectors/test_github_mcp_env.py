# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The GitHub connector must hand its token to the server under the name it reads (#4357).

``@modelcontextprotocol/server-github@2025.4.8`` only reads
``GITHUB_PERSONAL_ACCESS_TOKEN`` (``dist/common/utils.js``); under any other
name every call goes out anonymous.
"""

from __future__ import annotations

import keyring
import pytest

from gaia.connectors.catalog.mcp_servers import _GITHUB
from gaia.connectors.mcp_server import (
    McpServerHandler,
    _read_mcp_servers_json,
    _write_mcp_servers_json,
)
from gaia.connectors.store import SERVICE_NAME
from gaia.mcp.client.mcp_client import _resolve_keyring_refs

SERVER_TOKEN_VAR = "GITHUB_PERSONAL_ACCESS_TOKEN"


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr("gaia.connectors.mcp_server.Path.home", lambda: tmp_path)
    return tmp_path


def _clear_github_slots():
    for username in (f"{_GITHUB.id}:{SERVER_TOKEN_VAR}", f"{_GITHUB.id}:GITHUB_TOKEN"):
        try:
            keyring.delete_password(SERVICE_NAME, username)
        except keyring.errors.PasswordDeleteError:
            pass


@pytest.fixture(autouse=True)
def clean_keyring():
    _clear_github_slots()
    yield
    _clear_github_slots()


def _config_from_schema(token: str) -> dict:
    """Build the config the UI/CLI sends: one value per ``config_schema`` field."""
    return {field.key: token for field in _GITHUB.config_schema}


@pytest.mark.asyncio
async def test_launch_env_carries_token_under_server_var():
    token = "ghp_4357_launch_env"
    await McpServerHandler().configure(_GITHUB, _config_from_schema(token))

    entry = _read_mcp_servers_json()[_GITHUB.id]
    launch_env = _resolve_keyring_refs(entry["env"])

    assert launch_env == {SERVER_TOKEN_VAR: token}


@pytest.mark.asyncio
async def test_get_credential_returns_token_under_server_var():
    token = "ghp_4357_credential"
    handler = McpServerHandler()
    await handler.configure(_GITHUB, _config_from_schema(token))

    credential = await handler.get_credential(_GITHUB)

    assert credential["env"] == {SERVER_TOKEN_VAR: token}


@pytest.mark.asyncio
async def test_disconnect_removes_token_saved_under_old_var():
    old_ref = f"{SERVICE_NAME}:{_GITHUB.id}:GITHUB_TOKEN"
    keyring.set_password(SERVICE_NAME, f"{_GITHUB.id}:GITHUB_TOKEN", "ghp_old")
    _write_mcp_servers_json(
        {
            _GITHUB.id: {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": {"$keyring": old_ref}},
            }
        }
    )

    await McpServerHandler().disconnect(_GITHUB)

    assert keyring.get_password(SERVICE_NAME, f"{_GITHUB.id}:GITHUB_TOKEN") is None
    assert _GITHUB.id not in _read_mcp_servers_json()


@pytest.mark.asyncio
async def test_reconfigure_moves_token_off_old_var():
    old_username = f"{_GITHUB.id}:GITHUB_TOKEN"
    keyring.set_password(SERVICE_NAME, old_username, "ghp_old")
    _write_mcp_servers_json(
        {
            _GITHUB.id: {
                "command": "npx",
                "env": {"GITHUB_TOKEN": {"$keyring": f"{SERVICE_NAME}:{old_username}"}},
            }
        }
    )

    await McpServerHandler().configure(_GITHUB, _config_from_schema("ghp_new"))

    entry = _read_mcp_servers_json()[_GITHUB.id]
    assert _resolve_keyring_refs(entry["env"]) == {SERVER_TOKEN_VAR: "ghp_new"}
    assert keyring.get_password(SERVICE_NAME, old_username) is None


@pytest.mark.asyncio
async def test_disconnect_leaves_other_connectors_slots_alone():
    foreign = "mcp-tavily:TAVILY_API_KEY"
    keyring.set_password(SERVICE_NAME, foreign, "tvly_keep")
    _write_mcp_servers_json(
        {
            _GITHUB.id: {
                "command": "npx",
                "env": {"X": {"$keyring": f"{SERVICE_NAME}:{foreign}"}},
            }
        }
    )
    try:
        await McpServerHandler().disconnect(_GITHUB)

        assert keyring.get_password(SERVICE_NAME, foreign) == "tvly_keep"
    finally:
        keyring.delete_password(SERVICE_NAME, foreign)
