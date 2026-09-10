# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Port map regression tests (issue #3511).

The MCP bridge, Agent UI MCP server, TUI MCP server, and Telegram health
server share the loopback host scope, so each needs a distinct, canonical
default port. These tests pin the map in one place.
"""

import http.server
import os
import sys
import threading
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath("src"))

from gaia.cli import build_parser  # noqa: E402
from gaia.mcp import ports  # noqa: E402
from gaia.mcp import mcp_bridge  # noqa: E402
from gaia.mcp.servers import agent_ui_mcp, tui_mcp  # noqa: E402
from gaia.messaging import telegram  # noqa: E402


def test_canonical_port_map_values():
    assert ports.MCP_BRIDGE_PORT == 8765
    assert ports.AGENT_UI_MCP_PORT == 8766
    assert ports.TUI_MCP_PORT == 8767
    assert ports.TELEGRAM_HEALTH_PORT == 8768


def test_mcp_bridge_module_defaults_match_cli():
    """The standalone ``gaia-mcp`` console script must not drift from the map."""
    assert mcp_bridge.start_server.__defaults__[1] == ports.MCP_BRIDGE_PORT
    parser = mcp_bridge.build_parser()
    assert parser.parse_args([]).port == ports.MCP_BRIDGE_PORT


def test_agent_ui_mcp_module_default_matches_cli():
    assert agent_ui_mcp.MCP_DEFAULT_PORT == ports.AGENT_UI_MCP_PORT == 8766


def test_tui_mcp_module_default_matches_cli():
    assert tui_mcp.MCP_DEFAULT_PORT == ports.TUI_MCP_PORT == 8767


def test_cli_mcp_parser_defaults_follow_port_map():
    parser = build_parser()
    assert parser.parse_args(["mcp", "start"]).port == ports.MCP_BRIDGE_PORT
    assert parser.parse_args(["mcp", "status"]).port == ports.MCP_BRIDGE_PORT
    assert (
        parser.parse_args(["mcp", "test"]).port == ports.MCP_BRIDGE_PORT
    )
    assert (
        parser.parse_args(["mcp", "agent", "hello"]).port
        == ports.MCP_BRIDGE_PORT
    )
    assert parser.parse_args(["mcp", "serve"]).port == ports.AGENT_UI_MCP_PORT
    assert parser.parse_args(["mcp", "tui"]).port == ports.TUI_MCP_PORT


def test_cli_telegram_health_port_defaults():
    parser = build_parser()
    start_args = parser.parse_args(
        ["telegram", "start", "--token", "t", "--allowed-users", "1"]
    )
    assert start_args.health_port == ports.TELEGRAM_HEALTH_PORT
    status_args = parser.parse_args(["telegram", "status"])
    assert status_args.health_port == ports.TELEGRAM_HEALTH_PORT
    custom = parser.parse_args(
        [
            "telegram",
            "start",
            "--token",
            "t",
            "--allowed-users",
            "1",
            "--health-port",
            "18765",
        ]
    )
    assert custom.health_port == 18765


def _run_background_capture_health_port(monkeypatch, tmp_path, health_port_kwargs):
    """Start the adapter in background mode with a stubbed HTTPServer.

    Returns the ``(host, port)`` the health server was bound to.
    """
    bound = {}

    real_expanduser = os.path.expanduser

    def fake_expanduser(path):
        if path == "~":
            return str(tmp_path)
        if path.startswith(("~/", "~\\")):
            return str(tmp_path) + path[1:]
        return real_expanduser(path)

    monkeypatch.setattr(os.path, "expanduser", fake_expanduser)
    monkeypatch.delenv("GAIA_TEST_MODE", raising=False)
    monkeypatch.setitem(sys.modules, "telegram", MagicMock())
    monkeypatch.setitem(sys.modules, "telegram.ext", MagicMock())

    class FakeHTTPServer:
        def __init__(self, address, _handler):
            bound["address"] = address

        def handle_request(self):
            return

    monkeypatch.setattr(http.server, "HTTPServer", FakeHTTPServer)

    before = set(threading.enumerate())
    adapter = telegram.TelegramAdapter(token="fake", allowed_users={12345})
    adapter.start(token="fake", background=True, **health_port_kwargs)
    # The daemon threads shut themselves down once the stubbed
    # ``run_polling`` returns; join only the threads this test spawned so we
    # don't block on unrelated long-lived threads from earlier in the session.
    for thread in threading.enumerate():
        if thread in before or thread is threading.main_thread():
            continue
        thread.join(timeout=5)
    return bound.get("address")


def test_telegram_health_server_honors_passed_in_port(monkeypatch, tmp_path):
    address = _run_background_capture_health_port(
        monkeypatch, tmp_path, {"health_port": 18765}
    )
    assert address == ("127.0.0.1", 18765)


def test_telegram_health_server_default_port(monkeypatch, tmp_path):
    address = _run_background_capture_health_port(monkeypatch, tmp_path, {})
    assert address == ("127.0.0.1", ports.TELEGRAM_HEALTH_PORT)


def test_run_telegram_forwards_health_port(monkeypatch, tmp_path):
    seen = {}
    real_start = telegram.TelegramAdapter.start

    def spy_start(self, token, background=False, health_port=None):
        seen["health_port"] = health_port
        return real_start(
            self, token, background=background, health_port=health_port
        )

    monkeypatch.setattr(telegram.TelegramAdapter, "start", spy_start)
    bound_holder = {}

    real_expanduser = os.path.expanduser

    def fake_expanduser(path):
        if path == "~":
            return str(tmp_path)
        if path.startswith(("~/", "~\\")):
            return str(tmp_path) + path[1:]
        return real_expanduser(path)

    monkeypatch.setattr(os.path, "expanduser", fake_expanduser)
    monkeypatch.delenv("GAIA_TEST_MODE", raising=False)
    monkeypatch.setitem(sys.modules, "telegram", MagicMock())
    monkeypatch.setitem(sys.modules, "telegram.ext", MagicMock())

    class FakeHTTPServer:
        def __init__(self, address, _handler):
            bound_holder["address"] = address

        def handle_request(self):
            return

    monkeypatch.setattr(http.server, "HTTPServer", FakeHTTPServer)

    telegram.run_telegram(
        token="fake", allowed_users={12345}, background=True, health_port=18766
    )
    for thread in threading.enumerate():
        if thread is threading.main_thread():
            continue
        thread.join(timeout=5)
    assert seen["health_port"] == 18766
    assert bound_holder.get("address") == ("127.0.0.1", 18766)
