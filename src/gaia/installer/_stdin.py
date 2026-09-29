# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Shared stdin-TTY detection for installer commands.

A leaf module (no imports from sibling command modules) so both
``init_command.py`` and ``uninstall_command.py`` can detect whether stdin is
an interactive terminal without one command module importing another.
"""

from gaia.utils.terminal import stdin_is_interactive


def stdin_is_tty() -> bool:
    """Return True if stdin is a terminal a person is typing into."""
    return stdin_is_interactive()
