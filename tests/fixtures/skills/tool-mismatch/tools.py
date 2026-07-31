# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Registers only one of the two tools the manifest declares — on purpose."""

from gaia.agents.base.tools import tool


@tool
def present_tool(text: str) -> dict:
    """A tool that does exist."""
    return {"text": text}
