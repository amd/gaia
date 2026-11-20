# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""MCP server secrets go through the same insecure-backend refusal as every
other connector secret: nothing is read or written on a plaintext keyring, and
a disconnect still deletes, with a warning, since that only reduces exposure."""

from __future__ import annotations

import json
import logging

import keyring
import pytest

from gaia.connectors import keyring_access
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.mcp_server import McpServerHandler
from gaia.connectors.spec import ConnectorSpec
from gaia.connectors.store import SERVICE_NAME


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr("gaia.connectors.mcp_server.Path.home", lambda: tmp_path)
    return tmp_path


def _spec() -> ConnectorSpec:
    return ConnectorSpec(
        id="mcp-github",
        display_name="GitHub MCP",
        icon="G",
        category="dev-tools",
        tier=1,
        type="mcp_server",
        description="GitHub MCP server",
        mcp_command="npx",
        mcp_args=("-y", "@modelcontextprotocol/server-github"),
        mcp_env_keys=("GITHUB_TOKEN",),
    )


class PlaintextKeyring(keyring.backend.KeyringBackend):
    """Named like keyrings.alt's plaintext file backend; must never be touched."""

    priority = 1
    # Defining a backend registers it globally; keep keyring's chainer, which
    # other tests on this worker may build, from discovering this one.
    viable = False

    def get_password(self, service, username):
        raise AssertionError("must not read from a plaintext backend")

    def set_password(self, service, username, password):
        raise AssertionError("must not write to a plaintext backend")

    def delete_password(self, service, username):
        raise AssertionError("must not touch a plaintext backend")


class _DeletablePlaintextKeyring(PlaintextKeyring):
    """A plaintext backend a delete may still reach."""

    def __init__(self):
        super().__init__()
        self.deleted = []

    def delete_password(self, service, username):
        self.deleted.append((service, username))


@pytest.fixture
def plaintext_backend():
    previous = keyring.get_keyring()
    keyring.set_keyring(PlaintextKeyring())
    try:
        yield
    finally:
        keyring.set_keyring(previous)


@pytest.mark.asyncio
async def test_configure_refuses_a_plaintext_backend(plaintext_backend):
    with pytest.raises(ConnectorsError, match="Insecure keyring backend"):
        await McpServerHandler().configure(_spec(), {"GITHUB_TOKEN": "ghp_secret"})


@pytest.mark.asyncio
async def test_get_credential_refuses_a_plaintext_backend(plaintext_backend):
    with pytest.raises(ConnectorsError, match="Insecure keyring backend"):
        await McpServerHandler().get_credential(_spec())


@pytest.mark.asyncio
async def test_disconnect_on_a_plaintext_backend_still_deletes_and_warns(
    tmp_path, plaintext_backend, caplog
):
    path = tmp_path / ".gaia" / "mcp_servers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = {
        "mcpServers": {
            "mcp-github": {
                "command": "npx",
                "args": [],
                "env": {
                    "GITHUB_TOKEN": {
                        "$keyring": f"{SERVICE_NAME}:mcp-github:GITHUB_TOKEN"
                    }
                },
            }
        }
    }
    path.write_text(json.dumps(saved), encoding="utf-8")
    backend = _DeletablePlaintextKeyring()
    keyring.set_keyring(backend)
    keyring_access._warned.clear()

    with caplog.at_level(logging.WARNING, logger="gaia.connectors.keyring_access"):
        await McpServerHandler().disconnect(_spec())

    assert json.loads(path.read_text(encoding="utf-8")) == {"mcpServers": {}}
    assert backend.deleted == [(SERVICE_NAME, "mcp-github:GITHUB_TOKEN")]
    assert "insecure keyring backend" in caplog.text
    assert keyring_access.DOCS_URL in caplog.text


@pytest.mark.asyncio
async def test_a_secure_backend_still_round_trips():
    handler = McpServerHandler()
    await handler.configure(_spec(), {"GITHUB_TOKEN": "ghp_secret"})

    credential = await handler.get_credential(_spec())
    assert credential["env"] == {"GITHUB_TOKEN": "ghp_secret"}

    await handler.disconnect(_spec())
    assert keyring.get_password(SERVICE_NAME, "mcp-github:GITHUB_TOKEN") is None
