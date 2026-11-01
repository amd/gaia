# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""These tests cover the flagship's whole tool surface, so they run it on the full tier.

The default model is Gemma, whose compact tier withholds some tools; that tier
has its own tests in ``tests/unit/agents/test_model_features.py``.
"""

import pytest

from gaia.agents.base.model_features import TIER_ENV


# Module scope: module-scoped fixtures here build agents before any
# function-scoped fixture would run, and autouse ones go first within a scope.
@pytest.fixture(autouse=True, scope="module")
def _full_feature_tier():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(TIER_ENV, "full")
        yield
