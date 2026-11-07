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
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import keyring
import pytest

import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
from gaia.connectors.activations import activate_agent, list_agent_activations
from gaia.connectors.catalog.mcp_servers import (
    _SLACK,
    SLACK_BOT_SCOPES,
    SLACK_READ_TOOLS,
)
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


def test_only_read_tools_are_offered():
    """Join, leave and mark-read need scopes the app doesn't have; offered,
    the model asks to join channels instead of telling the user to invite it."""
    args = list(_SLACK.mcp_args)
    assert args[args.index("--enabled-tools") + 1].split(",") == list(SLACK_READ_TOOLS)
    for tool in SLACK_READ_TOOLS:
        assert not re.search(r"join|leave|mark|create|update|add|reaction", tool)


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
async def test_pasted_token_is_saved_without_surrounding_spaces(fake_home):
    # Slack rejects a padded token, so the server would fail to start.
    await McpServerHandler().configure(_SLACK, {TOKEN_VAR: f"  {TOKEN}\n"})

    assert keyring.get_password(SERVICE_NAME, SLOT) == TOKEN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    ["xapp-1-A0-signing", "8f14e45fceea167a5a36dedd4bea2543", "   "],
    ids=["app-level-token", "signing-secret", "only-spaces"],
)
async def test_a_value_that_is_not_a_bot_token_is_refused(fake_home, token):
    with pytest.raises(ConnectorsError, match="xoxb-"):
        await McpServerHandler().configure(_SLACK, {TOKEN_VAR: token})

    assert keyring.get_password(SERVICE_NAME, SLOT) is None
    assert _SLACK.id not in _read_mcp_servers_json()


def test_the_tile_refuses_a_wrong_token_with_a_clear_message(ui_api_client):
    resp = ui_api_client.post(
        "/api/connectors/mcp-slack/configure",
        json={"config": {TOKEN_VAR: "xapp-1-A0-signing"}},
        headers={"X-Gaia-UI": "1"},
    )
    assert resp.status_code == 400
    assert "xoxb-" in resp.json()["detail"]
    tile = ui_api_client.get("/api/connectors/mcp-slack").json()
    assert tile["configured"] is False


def test_the_tile_learns_the_token_prefix(ui_api_client):
    tile = ui_api_client.get("/api/connectors/mcp-slack").json()
    (field,) = tile["config_fields"]
    assert field["prefix"] == "xoxb-"


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
    # Test starts the server; a unit test stands in for the real npx process.
    started = []
    monkeypatch.setattr(
        "gaia.connectors.mcp_server._start_once",
        lambda name, entry: started.append(name) or {"ok": True, "detail": "started"},
    )
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
    assert started == ["mcp-slack"]
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


def _manifest_from_setup_url():
    url = urlparse(_SLACK.setup_url)
    assert (url.scheme, url.netloc, url.path) == ("https", "api.slack.com", "/apps")
    query = parse_qs(url.query)
    assert query["new_app"] == ["1"]
    return json.loads(query["manifest_json"][0])


def test_setup_link_creates_a_read_only_app_with_the_bot_scopes():
    manifest = _manifest_from_setup_url()
    assert manifest["oauth_config"]["scopes"]["bot"] == list(SLACK_BOT_SCOPES)
    assert manifest["features"]["bot_user"]["display_name"] == "GAIA"
    assert not any(":write" in s or s.startswith("chat:") for s in SLACK_BOT_SCOPES)


def test_setup_guide_lists_the_same_scopes_as_the_setup_link():
    guide = (Path(__file__).parents[3] / "docs" / "connectors" / "slack.mdx").read_text(
        encoding="utf-8"
    )
    listed = set(re.findall(r"`([a-z]+:(?:history|read))`", guide))
    assert listed == set(SLACK_BOT_SCOPES)


def test_tile_gets_the_setup_link_and_a_plain_field_label(ui_api_client):
    listing = ui_api_client.get("/api/connectors")
    tile = next(c for c in listing.json()["connectors"] if c["id"] == "mcp-slack")
    assert tile["setup_url"] == _SLACK.setup_url
    (field,) = tile["config_fields"]
    assert field["key"] == TOKEN_VAR
    assert field["label"] == "Slack bot token"
    assert field["placeholder"].startswith("xoxb-")
    assert "Create Slack app" in field["help_md"]
    assert "/invite @GAIA" in field["help_md"]
