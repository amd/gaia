# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA Installer Module

Provides functionality for:
- Installing Lemonade Server from GitHub releases
- Downloading required models for different profiles
- Initializing GAIA with a single command
"""

from gaia.installer.lemonade_installer import (
    InstallResult,
    LemonadeInfo,
    LemonadeInstaller,
)

__all__ = [
    "LemonadeInstaller",
    "LemonadeInfo",
    "InstallResult",
]
