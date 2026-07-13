#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Deprecated re-export shim.

``FileIOToolsMixin`` moved to ``gaia.agents.tools.file_io_tools`` when shared
tool mixins were promoted to the framework (#1396). Import from the new
location.
"""

import warnings

from gaia.agents.tools.file_io_tools import FileIOToolsMixin  # noqa: F401

warnings.warn(
    "gaia_agent_code.tools.file_io is deprecated; import from "
    "gaia.agents.tools.file_io_tools instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["FileIOToolsMixin"]
