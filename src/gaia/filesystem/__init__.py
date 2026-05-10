# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""GAIA file system indexing and categorization."""

from gaia.filesystem.categorizer import auto_categorize
from gaia.filesystem.index import FileSystemIndexService

__all__ = ["FileSystemIndexService", "auto_categorize"]
