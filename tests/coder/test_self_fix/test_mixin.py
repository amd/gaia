# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Smoke test for :class:`gaia.coder.self_fix.SelfFixToolsMixin`."""

from __future__ import annotations

from gaia.coder.self_fix import SelfFixToolsMixin


def test_self_fix_tools_mixin_registers_at_least_seven_tools() -> None:
    """The §15.2 contract requires ≥ 7 registered tools on the mixin."""
    mixin = SelfFixToolsMixin()
    registered = mixin.register_self_fix_tools()
    assert len(registered) >= 7
    # Sanity: no duplicates.
    assert len(set(registered)) == len(registered)
    # Canonical tools required by the §7.4 loop.
    for required in (
        "triage_feedback",
        "localise_feedback",
        "draft_fix_plan",
        "apply_self_fix",
        "write_self_fix_regression_test",
        "publish_self_fix_pr",
        "critique_turn_output",
    ):
        assert required in registered
