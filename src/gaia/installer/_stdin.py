# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Shared stdin-TTY detection for installer commands.

A leaf module (no imports from sibling command modules) so both
``init_command.py`` and ``uninstall_command.py`` can detect whether stdin is
an interactive terminal without one command module importing another.
"""

import sys


def stdin_is_tty() -> bool:
    """Return True if stdin looks like an interactive terminal.

    False on any of the ways a non-interactive run can make ``isatty()``
    itself raise (e.g. closed stdin), not just when it returns False.
    """
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError, OSError):
        return False
