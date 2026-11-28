# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Human-readable byte sizes."""


def format_size(size_bytes: int, gb_decimals: int = 1) -> str:
    """Format a byte count as ``"512 B"``, ``"1.5 KB"``, ``"3.0 MB"`` or ``"1.0 GB"``.

    Args:
        size_bytes: The size in bytes.
        gb_decimals: Decimal places shown for gigabytes; KB and MB always show one.
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.{gb_decimals}f} GB"
