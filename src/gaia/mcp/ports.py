# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Canonical default ports for GAIA's local MCP/health surfaces.

Port map (each surface binds loopback by default):

- 8765 ``MCP_BRIDGE_PORT`` — MCP bridge (``gaia mcp start``/``status``/``test``/``agent``).
- 8766 ``AGENT_UI_MCP_PORT`` — Agent UI MCP server (``gaia mcp serve``).
- 8767 ``TUI_MCP_PORT`` — TUI control MCP server (``gaia mcp tui``).
- 8765 ``TELEGRAM_HEALTH_PORT`` — Telegram adapter ``/healthz`` probe. Shares the
  bridge's numeric value but is a separate constant because it is a different
  surface; pass ``--health-port`` to move it when both run on one machine.
"""

MCP_BRIDGE_PORT = 8765
AGENT_UI_MCP_PORT = 8766
TUI_MCP_PORT = 8767
TELEGRAM_HEALTH_PORT = 8765
