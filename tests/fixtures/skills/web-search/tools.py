# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tools provided by the ``web-search`` fixture skill."""

from gaia.agents.base.tools import tool


@tool
def search_web(query: str, max_results: int = 5) -> dict:
    """Search the web for current information."""
    return {"query": query, "results": [], "max_results": max_results}
