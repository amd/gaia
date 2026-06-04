# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Deprecated re-export shim.

``RAGToolsMixin`` moved to ``gaia.agents.tools.rag_tools`` when shared tool
mixins were promoted to the framework (#1396). Import from the new location.
"""

import warnings

from gaia.agents.tools.rag_tools import (  # noqa: F401
    RAGToolsMixin,
    extract_page_from_chunk,
)

warnings.warn(
    "gaia.agents.chat.tools.rag_tools is deprecated; import from "
    "gaia.agents.tools.rag_tools instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["RAGToolsMixin", "extract_page_from_chunk"]
