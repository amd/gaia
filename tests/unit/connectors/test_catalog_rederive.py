# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Saved MCP connectors follow the current catalog when they load (#4358).

``mcp_servers.json`` keeps whatever ``configure`` wrote at the time, so a later
catalog fix (a version pin, a new command, a renamed env var) never reached
people who had already connected until they reconfigured.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import keyring
import pytest

from gaia.connectors.catalog.mcp_servers import _GITHUB, _MEMORY
from gaia.connectors.mcp_server import McpServerHandler, _rederived_env
from gaia.connectors.store import SERVICE_NAME
from gaia.mcp.client.config import MCPConfig
from gaia.mcp.client.mcp_client import _resolve_keyring_refs

OLD_GITHUB_SLOT = f"{_GITHUB.id}:GITHUB_TOKEN"


@pytest.fixture(autouse=True)
def gaia_home(tmp_path, monkeypatch):
    home = tmp_path / "gaia-home"
    home.mkdir()
    monkeypatch.setenv("GAIA_HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    return home


@pytest.fixture(autouse=True)
def clean_keyring():
    yield
    for username in (OLD_GITHUB_SLOT, f"{_GITHUB.id}:GITHUB_PERSONAL_ACCESS_TOKEN"):
        try:
            keyring.delete_password(SERVICE_NAME, username)
        except keyring.errors.PasswordDeleteError:
            pass


def _write(path, servers):
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def test_saved_entry_picks_up_catalog_command_and_keeps_user_settings(gaia_home):
    _write(
        gaia_home / "mcp_servers.json",
        {
            _MEMORY.id: {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-memory@0.0.1"],
                "env": {},
                "disabled": True,
                "timeout": 60,
            }
        },
    )

    entry = MCPConfig().get_server(_MEMORY.id)

    assert entry["command"] == _MEMORY.mcp_command
    assert entry["args"] == list(_MEMORY.mcp_args)
    assert entry["disabled"] is True
    assert entry["timeout"] == 60


def test_renamed_env_key_reads_the_secret_saved_under_the_old_name(gaia_home):
    _save_github_before_rename(gaia_home)

    entry = MCPConfig().get_server(_GITHUB.id)

    assert _resolve_keyring_refs(entry["env"]) == {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_saved_before_rename"
    }


def _save_github_before_rename(gaia_home):
    keyring.set_password(SERVICE_NAME, OLD_GITHUB_SLOT, "ghp_saved_before_rename")
    _write(
        gaia_home / "mcp_servers.json",
        {
            _GITHUB.id: {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {
                    "GITHUB_TOKEN": {"$keyring": f"{SERVICE_NAME}:{OLD_GITHUB_SLOT}"}
                },
                "disabled": False,
            }
        },
    )


def test_hand_added_plain_env_value_is_kept(gaia_home):
    _write(
        gaia_home / "mcp_servers.json",
        {
            _GITHUB.id: {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {
                    "GITHUB_TOKEN": {"$keyring": f"{SERVICE_NAME}:{OLD_GITHUB_SLOT}"},
                    "GITHUB_HOST": "https://github.example.com",
                },
            }
        },
    )

    env = MCPConfig().get_server(_GITHUB.id)["env"]

    assert env == {
        "GITHUB_HOST": "https://github.example.com",
        "GITHUB_PERSONAL_ACCESS_TOKEN": {
            "$keyring": f"{SERVICE_NAME}:{OLD_GITHUB_SLOT}"
        },
    }


@pytest.mark.asyncio
async def test_connector_test_passes_for_a_token_saved_before_rename(gaia_home):
    _save_github_before_rename(gaia_home)

    result = await McpServerHandler().test(_GITHUB)

    assert result["ok"] is True


@pytest.mark.asyncio
async def test_get_credential_returns_a_token_saved_before_rename(gaia_home):
    _save_github_before_rename(gaia_home)

    credential = await McpServerHandler().get_credential(_GITHUB)

    assert credential["env"] == {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_saved_before_rename"
    }


@pytest.mark.asyncio
async def test_connector_test_still_reports_a_missing_token(gaia_home):
    _write(
        gaia_home / "mcp_servers.json",
        {_GITHUB.id: {"command": "npx", "args": [], "env": {}, "disabled": False}},
    )

    result = await McpServerHandler().test(_GITHUB)

    assert result["ok"] is False


def test_hand_added_server_is_left_as_written(gaia_home):
    custom = {"command": "python", "args": ["my_server.py"], "env": {"X": "1"}}
    _write(gaia_home / "mcp_servers.json", {"my-server": custom})

    assert MCPConfig().get_server("my-server") == custom


def test_project_local_file_is_left_as_written():
    local = {"command": "node", "args": ["./my-github-fork.js"], "env": {}}
    _write(Path.cwd() / "mcp_servers.json", {_GITHUB.id: local})

    assert MCPConfig().get_server(_GITHUB.id) == local


def test_loading_does_not_rewrite_the_file(gaia_home):
    path = gaia_home / "mcp_servers.json"
    _write(path, {_MEMORY.id: {"command": "npx", "args": ["old"], "env": {}}})
    before = path.read_bytes()

    MCPConfig()

    assert path.read_bytes() == before


def test_two_renamed_keys_are_not_guessed():
    spec = dataclasses.replace(_GITHUB, mcp_env_keys=("NEW_A", "NEW_B"))
    entry = {
        "env": {
            "OLD_A": {"$keyring": f"{SERVICE_NAME}:{spec.id}:OLD_A"},
            "OLD_B": {"$keyring": f"{SERVICE_NAME}:{spec.id}:OLD_B"},
        }
    }

    assert _rederived_env(spec, entry) == {
        "NEW_A": {"$keyring": f"{SERVICE_NAME}:{spec.id}:NEW_A"},
        "NEW_B": {"$keyring": f"{SERVICE_NAME}:{spec.id}:NEW_B"},
    }


def test_ref_outside_the_connector_namespace_is_never_followed():
    entry = {"env": {"GITHUB_TOKEN": {"$keyring": f"{SERVICE_NAME}:mcp-slack:TOKEN"}}}

    assert _rederived_env(_GITHUB, entry) == {
        "GITHUB_PERSONAL_ACCESS_TOKEN": {
            "$keyring": f"{SERVICE_NAME}:{_GITHUB.id}:GITHUB_PERSONAL_ACCESS_TOKEN"
        }
    }
