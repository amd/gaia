# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for ``gaia_agent_email.context_budget`` (issue #1892).

Pins the email agent eval path's context-window envelope: a 16K target
(the size everyday triage/draft prompts should fit under) and a 32K hard
max (the ceiling the agent must never request above).
"""

from __future__ import annotations

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.
import pytest  # noqa: E402

pytest.importorskip("gaia_agent_email")  # noqa: E402


def test_context_budget_values():
    import gaia_agent_email.context_budget as context_budget
    from gaia_agent_email.context_budget import (
        CONTEXT_MAX_TOKENS,
        CONTEXT_TARGET_TOKENS,
    )

    assert CONTEXT_TARGET_TOKENS == 16384
    assert CONTEXT_MAX_TOKENS == 32768
    assert len(context_budget.__doc__ or "") > 50
