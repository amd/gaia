# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Deprecated re-export shim.

``CodeIndexToolsMixin`` moved to ``gaia.agents.tools.code_index_tools`` when
shared tool mixins were promoted to the framework (#1396). Import from the new
location.
"""

import warnings

from gaia.agents.tools.code_index_tools import (  # noqa: F401
    _CODE_INDEX_AVAILABLE,
    _MISSING_DEPS_MSG,
    CodeIndexToolsMixin,
)

warnings.warn(
    "gaia.agents.code_index.tools.mixin is deprecated; import from "
    "gaia.agents.tools.code_index_tools instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["CodeIndexToolsMixin"]
