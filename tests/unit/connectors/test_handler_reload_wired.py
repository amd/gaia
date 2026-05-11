# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Reload-callback wiring at FastAPI lifespan startup (#1004).

The ``McpServerHandler`` singleton is constructed at module import time
(``handler.py:_HANDLER_REGISTRY``) — well before the FastAPI app's chat-
session agent cache exists. The lifespan startup hook calls
``handler.set_reload_callback(reload_all_session_agents_mcp)`` so that a
subsequent ``configure`` / ``disconnect`` / ``set_enabled`` reaches every
cached agent's per-instance ``MCPClientManager``.

These tests check the wiring contract without spawning real MCP servers
or chat agents — the wiring itself is what's worth pinning.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def reset_handler_callback():
    """
    Snapshot and restore the McpServerHandler reload callback around each
    test, so a test that wires (or unwires) it doesn't leak into others.
    """
    from gaia.connectors.handler import _HANDLER_REGISTRY

    handler = _HANDLER_REGISTRY.get("mcp_server")
    if handler is None:
        pytest.skip("McpServerHandler not registered")

    original = getattr(handler, "_reload", None)
    yield handler
    handler._reload = original


def test_lifespan_wires_reload_callback(ui_api_client, reset_handler_callback):
    """After the FastAPI app starts, the handler's reload callback is set."""
    handler = reset_handler_callback
    # ui_api_client fixture has already triggered the lifespan startup hook
    # by the time this test runs. The wired callback should be the helper
    # from `_chat_helpers`.
    assert handler._reload is not None, (
        "Lifespan startup did not wire the McpServerHandler reload_callback. "
        "Toggling a connector via /api/connectors/{id}/disable would not "
        "take effect until GAIA restart."
    )

    # Sanity: the wired function comes from _chat_helpers (not some unrelated
    # callable that happened to land there).
    from gaia.ui._chat_helpers import reload_all_session_agents_mcp

    assert handler._reload is reload_all_session_agents_mcp


def test_set_enabled_invokes_session_reload(
    ui_api_client, reset_handler_callback, monkeypatch, tmp_path
):
    """
    End-to-end: hitting POST /api/connectors/.../disable triggers the
    wired reload_all_session_agents_mcp helper.
    """
    handler = reset_handler_callback

    # Replace the wired callback with a mock that just records the call.
    mock_reload = MagicMock()
    handler.set_reload_callback(mock_reload)

    # Pre-write an mcp_servers.json entry so the route can find it. The
    # lifespan-bound REGISTRY already contains the catalog entries.
    import json

    monkeypatch.setattr("gaia.connectors.mcp_server.Path.home", lambda: tmp_path)
    gaia_dir = tmp_path / ".gaia"
    gaia_dir.mkdir(parents=True, exist_ok=True)
    (gaia_dir / "mcp_servers.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "mcp-github": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-github"],
                        "env": {
                            "GITHUB_TOKEN": {
                                "$keyring": ("gaia.connections:mcp-github:GITHUB_TOKEN")
                            }
                        },
                        "disabled": False,
                    }
                }
            }
        )
    )

    resp = ui_api_client.post(
        "/api/connectors/mcp-github/disable",
        headers={"x-gaia-ui": "1"},
    )
    assert resp.status_code == 200, resp.text

    # The wired callback fired exactly once.
    assert mock_reload.call_count == 1


def test_reload_helper_is_resilient_to_per_session_failures(monkeypatch):
    """
    `reload_all_session_agents_mcp` must NOT propagate an exception from
    one bad session — otherwise toggling fails the request when only one
    cached agent is in a bad state.
    """
    from gaia.ui import _chat_helpers

    bad_agent = type(
        "BadAgent",
        (),
        {
            "_mcp_manager": type(
                "BadManager",
                (),
                {"reload": lambda self: (_ for _ in ()).throw(RuntimeError("boom"))},
            )()
        },
    )()
    good_manager_calls: list[int] = []
    good_agent = type(
        "GoodAgent",
        (),
        {
            "_mcp_manager": type(
                "GoodManager",
                (),
                {"reload": lambda self: good_manager_calls.append(1)},
            )()
        },
    )()

    fake_cache = {
        "session-bad": {"agent": bad_agent, "model_id": "m", "agent_type": "chat"},
        "session-good": {"agent": good_agent, "model_id": "m", "agent_type": "chat"},
    }
    monkeypatch.setattr(_chat_helpers, "_agent_cache", fake_cache)

    count = _chat_helpers.reload_all_session_agents_mcp()

    # Good session was reloaded; bad session failed quietly.
    assert good_manager_calls == [1]
    assert count == 1


def test_reload_helper_skips_agents_without_mcp_manager(monkeypatch):
    """An agent without ``_mcp_manager`` (some custom registry agent) must
    be silently skipped, not crash the broadcast."""
    from gaia.ui import _chat_helpers

    plain_agent = object()  # no _mcp_manager attribute
    fake_cache = {
        "session-plain": {
            "agent": plain_agent,
            "model_id": "m",
            "agent_type": "chat",
        },
    }
    monkeypatch.setattr(_chat_helpers, "_agent_cache", fake_cache)

    count = _chat_helpers.reload_all_session_agents_mcp()
    assert count == 0
