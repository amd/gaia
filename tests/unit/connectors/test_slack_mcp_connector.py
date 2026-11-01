# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Slack MCP connector (#1002): pinned launch, keyring-only token, clean disconnect.

The bot token must reach the server only through the keyring: never in
``mcp_servers.json``, the launch argv, an API response, an SSE event, or a log
line. Disconnect must clear the keyring slot, the entry, and every grant.
"""

from __future__ import annotations

import json
import logging

import keyring
import pytest

import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
from gaia.connectors.activations import activate_agent, list_agent_activations
from gaia.connectors.catalog.mcp_servers import _SLACK
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.grants import grant_agent, list_agent_grants
from gaia.connectors.mcp_server import McpServerHandler, _read_mcp_servers_json
from gaia.connectors.registry import REGISTRY
from gaia.connectors.store import SERVICE_NAME
from gaia.mcp.client.mcp_client import _resolve_keyring_refs

from .test_catalog_pins import package_spec_problem

TOKEN_VAR = "SLACK_MCP_XOXB_TOKEN"
SLOT = f"{_SLACK.id}:{TOKEN_VAR}"
TOKEN = "xoxb-1002-SENTINEL-DO-NOT-LEAK-7c1e"


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    for module in ("mcp_server", "grants", "activations"):
        monkeypatch.setattr(f"gaia.connectors.{module}.Path.home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / ".gaia"))
    return tmp_path


@pytest.fixture(autouse=True)
def clean_slot():
    def _clear():
        try:
            keyring.delete_password(SERVICE_NAME, SLOT)
        except keyring.errors.PasswordDeleteError:
            pass

    _clear()
    yield
    _clear()


def _files_containing(root, needle):
    hits = []
    for path in root.rglob("*"):
        if path.is_file() and needle in path.read_text("utf-8", errors="ignore"):
            hits.append(path)
    return hits


# ---------------------------------------------------------------------------
# Spec shape
# ---------------------------------------------------------------------------


def test_spec_is_registered_as_mcp_server():
    spec = REGISTRY.get("mcp-slack")
    assert spec is _SLACK
    assert spec.type == "mcp_server"
    assert spec.docs_url == "https://amd-gaia.ai/docs/connectors/slack"


def test_launch_is_pinned_and_speaks_stdio():
    assert package_spec_problem(_SLACK.mcp_command, _SLACK.mcp_args) is None
    args = list(_SLACK.mcp_args)
    # The server picks its transport from argv; without it, it won't speak stdio.
    assert args[args.index("--transport") + 1] == "stdio"


def test_deprecated_reference_package_is_not_used():
    assert not any("@modelcontextprotocol/server-slack" in a for a in _SLACK.mcp_args)


def test_secret_fields_and_env_keys_are_the_same_set():
    secret_keys = {f.key for f in _SLACK.config_schema if f.secret}
    assert secret_keys == set(_SLACK.mcp_env_keys) == {TOKEN_VAR}
    for field in _SLACK.config_schema:
        assert (field.kind == "secret") == field.secret, field.key


def test_no_secret_rides_in_argv():
    # argv is visible to every local user in the process list.
    for arg in _SLACK.mcp_args:
        assert "xox" not in arg.lower()
        assert TOKEN_VAR not in arg


# ---------------------------------------------------------------------------
# Handler lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_configure_keeps_token_in_keyring_only(fake_home, caplog):
    caplog.set_level(logging.DEBUG)
    result = await McpServerHandler().configure(_SLACK, {TOKEN_VAR: TOKEN})

    assert keyring.get_password(SERVICE_NAME, SLOT) == TOKEN
    entry = _read_mcp_servers_json()[_SLACK.id]
    assert entry["command"] == "npx"
    assert entry["args"] == list(_SLACK.mcp_args)
    assert entry["env"] == {TOKEN_VAR: {"$keyring": f"{SERVICE_NAME}:{SLOT}"}}
    assert _resolve_keyring_refs(entry["env"]) == {TOKEN_VAR: TOKEN}

    assert TOKEN not in json.dumps(result)
    assert TOKEN not in caplog.text
    assert _files_containing(fake_home, TOKEN) == []


@pytest.mark.asyncio
async def test_configure_without_token_writes_nothing(fake_home):
    with pytest.raises(ConnectorsError, match=TOKEN_VAR):
        await McpServerHandler().configure(_SLACK, {})

    assert keyring.get_password(SERVICE_NAME, SLOT) is None
    assert _SLACK.id not in _read_mcp_servers_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    ['xoxb-"quoted"-\\back\\slash', "xoxb-line\nbreak", "xoxb-ünïcödé-🔑"],
    ids=["json-escapes", "newline", "unicode"],
)
async def test_awkward_token_round_trips_without_touching_disk(fake_home, token):
    await McpServerHandler().configure(_SLACK, {TOKEN_VAR: token})

    entry = _read_mcp_servers_json()[_SLACK.id]
    assert _resolve_keyring_refs(entry["env"]) == {TOKEN_VAR: token}
    for path in fake_home.rglob("*"):
        if path.is_file():
            raw = path.read_text("utf-8", errors="ignore")
            assert token not in raw
            assert json.dumps(token)[1:-1] not in raw


@pytest.mark.asyncio
async def test_disconnect_clears_keyring_entry_and_grants(fake_home):
    handler = McpServerHandler()
    await handler.configure(_SLACK, {TOKEN_VAR: TOKEN})
    grant_agent(_SLACK.id, "builtin:chat", ["use"])
    grant_agent(_SLACK.id, "builtin:gaia", ["use"])
    activate_agent(_SLACK.id, "builtin:chat")

    await handler.disconnect(_SLACK)

    assert keyring.get_password(SERVICE_NAME, SLOT) is None
    assert _SLACK.id not in _read_mcp_servers_json()
    assert list_agent_grants(_SLACK.id) == {}
    assert not any(list_agent_activations(_SLACK.id).values())

    # Re-adding must not inherit the old consent.
    await handler.configure(_SLACK, {TOKEN_VAR: "xoxb-second-lifecycle"})
    assert list_agent_grants(_SLACK.id) == {}


# ---------------------------------------------------------------------------
# Agent UI API + SSE
# ---------------------------------------------------------------------------


def test_api_lifecycle_never_echoes_token(ui_api_client, monkeypatch, caplog):
    from gaia.connectors.handler import _HANDLER_REGISTRY
    from gaia.ui.routers import connectors as router

    events = []

    async def _record(event_type, payload):
        events.append({"type": event_type, "payload": payload})

    monkeypatch.setattr(router._emitter, "emit", _record)
    # Keep the real reload hook from rebuilding session agents mid-test.
    monkeypatch.setattr(_HANDLER_REGISTRY["mcp_server"], "_reload", None)
    caplog.set_level(logging.DEBUG)
    headers = {"X-Gaia-UI": "1"}

    listing = ui_api_client.get("/api/connectors")
    assert listing.status_code == 200
    tile = next(c for c in listing.json()["connectors"] if c["id"] == "mcp-slack")
    assert tile["configured"] is False

    configured = ui_api_client.post(
        "/api/connectors/mcp-slack/configure",
        json={"config": {TOKEN_VAR: TOKEN}},
        headers=headers,
    )
    assert configured.status_code == 200, configured.text
    summary = ui_api_client.get("/api/connectors/mcp-slack")
    tested = ui_api_client.post("/api/connectors/mcp-slack/test", headers=headers)
    assert tested.json()["ok"] is True
    deleted = ui_api_client.delete("/api/connectors/mcp-slack", headers=headers)
    assert deleted.status_code == 204

    for resp in (listing, configured, summary, tested, deleted):
        assert TOKEN not in resp.text
    assert [e["type"] for e in events] == [
        "connector.configured",
        "connector.tested",
        "connector.disconnected",
    ]
    assert TOKEN not in json.dumps(events)
    assert TOKEN not in caplog.text
    assert keyring.get_password(SERVICE_NAME, SLOT) is None
