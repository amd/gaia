# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Agent UI's global memory switch, read the same way everywhere."""

MEMORY_ENABLED_KEY = "memory_enabled"


def memory_enabled(db) -> bool:
    """Whether the Agent UI stores memory. On unless the user turned it off.

    The TUI remembers by default; an Agent UI that silently didn't would make
    the same request behave differently depending on the window it was typed in.
    """
    return db.get_setting(MEMORY_ENABLED_KEY, "true") == "true"
