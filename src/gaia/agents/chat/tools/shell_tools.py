# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Deprecated re-export shim.

``ShellToolsMixin`` moved to ``gaia.agents.tools.shell_tools`` when shared
tool mixins were promoted to the framework (#1396). Import from the new
location.
"""

import warnings

from gaia.agents.tools.shell_tools import (  # noqa: F401
    ALLOWED_COMMANDS,
    DANGEROUS_PS_PATTERNS,
    DANGEROUS_SHELL_OPERATORS,
    SAFE_GIT_COMMANDS,
    SAFE_PS_CMDLET_PREFIXES,
    ShellToolsMixin,
)

warnings.warn(
    "gaia.agents.chat.tools.shell_tools is deprecated; import from "
    "gaia.agents.tools.shell_tools instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "ALLOWED_COMMANDS",
    "DANGEROUS_PS_PATTERNS",
    "DANGEROUS_SHELL_OPERATORS",
    "SAFE_GIT_COMMANDS",
    "SAFE_PS_CMDLET_PREFIXES",
    "ShellToolsMixin",
]
