# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Canonical default ports for GAIA's local MCP/health surfaces.

Port map (each surface binds loopback by default):

- 8765 ``MCP_BRIDGE_PORT`` — MCP bridge (``gaia mcp start``/``status``/``test``/``agent``).
- 8766 ``AGENT_UI_MCP_PORT`` — Agent UI MCP server (``gaia mcp serve``).
- 8767 ``TUI_MCP_PORT`` — TUI control MCP server (``gaia mcp tui``).
- 8768 ``TELEGRAM_HEALTH_PORT`` — Telegram adapter ``/healthz`` probe. Every
  surface gets its own default so the MCP bridge and the Telegram health
  server do not collide on one machine; pass ``--health-port`` to move it.
- 8769 ``SLACK_HEALTH_PORT`` — Slack adapter ``/healthz`` probe. Socket Mode
  itself needs no inbound port (the connection is an outbound WebSocket);
  this is only so a supervisor can ask whether the bridge is alive.
"""

MCP_BRIDGE_PORT = 8765
AGENT_UI_MCP_PORT = 8766
TUI_MCP_PORT = 8767
TELEGRAM_HEALTH_PORT = 8768
SLACK_HEALTH_PORT = 8769
