# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Whether an agent may change memory now: the one check every writer asks."""

from typing import Optional


def memory_off_reason(agent) -> str:
    """Why *agent* is not storing memory, in words the user can act on."""
    reason = getattr(agent, "_incognito_reason", None)
    if reason == "private":
        return "This is a private chat, so nothing is saved to memory."
    if reason == "memory_off":
        return (
            "Memory is turned off in Settings, so nothing is saved. "
            "It can be turned back on there."
        )
    return "Memory is off for this session, so nothing is saved."


def memory_writes_refused(agent) -> Optional[str]:
    """Why *agent* may not change memory now, or None.

    Every memory writer goes through this: the memory tools, extraction,
    lessons, procedures and use counts. A private chat or memory-off changes
    nothing stored, not even by reading it.
    """
    if getattr(agent, "_incognito", False):
        return memory_off_reason(agent)
    return None
