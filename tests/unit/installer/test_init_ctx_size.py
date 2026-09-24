# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Every `gaia init` profile declares the context size its verify step loads at."""

from gaia.installer.init_command import INIT_PROFILES


def test_all_profiles_define_min_context_size():
    """Every shipped profile must declare min_context_size so init verifies the
    model at an explicit ctx size rather than Lemonade's small default."""
    for name, profile in INIT_PROFILES.items():
        assert (
            "min_context_size" in profile
        ), f"profile {name!r} missing min_context_size"
        assert isinstance(profile["min_context_size"], int)
        assert profile["min_context_size"] > 0
