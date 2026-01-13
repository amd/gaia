# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
GAIA Jira App - Natural language interface for Atlassian tools via MCP.
"""

from .app import JiraApp, main

__all__ = ["main", "JiraApp"]
