# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""GAIA utilities."""

from gaia.utils.file_watcher import (
    FileChangeHandler,
    FileWatcher,
    FileWatcherMixin,
    compute_bytes_hash,
    compute_file_hash,
)
from gaia.utils.parsing import (
    detect_field_changes,
    extract_json_from_text,
    pdf_page_to_image,
    validate_required_fields,
)

__all__ = [
    # File watching
    "FileChangeHandler",
    "FileWatcher",
    "FileWatcherMixin",
    # File hashing
    "compute_file_hash",
    "compute_bytes_hash",
    # Parsing utilities
    "extract_json_from_text",
    "pdf_page_to_image",
    "detect_field_changes",
    "validate_required_fields",
]
