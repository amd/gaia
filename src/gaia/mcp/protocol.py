# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""MCP protocol revisions GAIA speaks, shared by its MCP client and bridge."""

# Handshake-era MCP revisions; 2026-07-28 dropped `initialize`, so it is not offered.
SUPPORTED_PROTOCOL_VERSIONS = frozenset(
    {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
)
MCP_PROTOCOL_VERSION = "2025-11-25"
