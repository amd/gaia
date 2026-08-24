# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tools for the word-count capture fixture (eval + unit tests)."""

from gaia.agents.base.tools import tool


@tool
def count_words(text: str) -> int:
    """Count the words in a text."""
    return len(text.split())
