# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Governance-layer exceptions."""


class GaiaGovernanceError(Exception):
    """Base error for the GAIA governance package."""


class CheckpointNotFoundError(GaiaGovernanceError):
    """Raised when a checkpoint cannot be found."""


class InvalidResolutionError(GaiaGovernanceError):
    """Raised when a checkpoint resolution is invalid for its current state."""
