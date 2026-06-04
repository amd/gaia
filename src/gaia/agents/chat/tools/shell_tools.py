# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Deprecated re-export shim.

``ShellToolsMixin`` moved to ``gaia.agents.tools.shell_tools`` when shared
tool mixins were promoted to the framework (#1396). Import from the new
location.
"""

import warnings

from gaia.agents.tools import shell_tools as _shell_tools
from gaia.agents.tools.shell_tools import *  # noqa: F401,F403

warnings.warn(
    "gaia.agents.chat.tools.shell_tools is deprecated; import from "
    "gaia.agents.tools.shell_tools instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = getattr(
    _shell_tools,
    "__all__",
    [name for name in dir(_shell_tools) if not name.startswith("_")],
)
