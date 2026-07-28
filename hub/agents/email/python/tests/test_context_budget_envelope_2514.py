# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fixed contract (#2514): device-profile-aware envelope budget.

``list_inbox``/``search_messages`` currently cap each message body
independently (``DEFAULT_BODY_LIMIT_CHARS`` per message) with no COMBINED
cap on the whole tool result — 25 realistic messages can overflow the NPU
profile's 32768-token context window on the very first tool call of a fresh
conversation (issue #2514). This file pins the two ``context_budget.py``
primitives the fix needs:

1. ``envelope_budget_tokens(ctx_size: Optional[int] = None)`` — MODIFIED
   signature. ``ctx_size=None`` (the default) must keep returning EXACTLY
   today's value so existing zero-arg callers (``triage_condense.py``) are
   unaffected (non-regression). An explicit ``ctx_size`` uses THAT as the
   base instead of ``CONTEXT_TARGET_TOKENS``, floored at 0.
2. ``active_profile_ctx_size()`` — NEW. Resolves the ACTIVE device profile's
   context window (65536 gpu/cpu, 32768 npu) via
   ``gaia.llm.lemonade_client.profile_ctx_size(GaiaConfig.load().default_device)``.

TDD split (red/green): neither ``ctx_size`` param nor ``active_profile_ctx_size``
exist on current ``main`` — this file is the RED pin for both. The bare
``envelope_budget_tokens()`` non-regression case is the one assertion in this
file that is expected GREEN already (today's zero-arg call already returns
that value); everything else is RED until the implementation lands.

Hermetic: no Lemonade, no network. ``GaiaConfig.load`` is monkeypatched (never
a real ``~/.gaia/config.json`` write, never shell ``HOME=``) for the two
device-profile branches; one additional smoke test calls the REAL
``GaiaConfig.load()`` against whatever HOME this test process has, asserting
only that a valid profile ctx size comes back (not a specific value — that
value depends on the environment, which this suite doesn't control).

Every expected number is derived from imported module constants
(``CONTEXT_TARGET_TOKENS``, ``_AGENT_LOOP_FIXED_TOKENS``,
``_RESPONSE_RESERVE_TOKENS``, ``GPU_CTX_SIZE``, ``NPU_CTX_SIZE``) — never a
hardcoded literal duplicating a constant's value.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path / import bootstrap (mirrors test_read_tools_thread_budget.py)
# ---------------------------------------------------------------------------

# parents[0] = tests/, [1] = python/, [2] = email/, [3] = agents/, [4] = hub/,
# [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

# ``envelope_budget_tokens`` already exists today (zero-arg) — safe to import
# at module level; the new ``ctx_size`` kwarg is exercised per-test below.
# ``active_profile_ctx_size`` does NOT exist yet, so it is imported lazily
# inside each test that needs it (a module-level import here would turn
# EVERY test in this file into a collection error instead of letting the
# ``envelope_budget_tokens`` tests run and fail/pass on their own merits).
from gaia_agent_email.context_budget import (  # noqa: E402
    _AGENT_LOOP_FIXED_TOKENS,
    _RESPONSE_RESERVE_TOKENS,
    CONTEXT_TARGET_TOKENS,
    envelope_budget_tokens,
)

from gaia.config import GaiaConfig  # noqa: E402
from gaia.llm.lemonade_client import GPU_CTX_SIZE, NPU_CTX_SIZE  # noqa: E402

# ---------------------------------------------------------------------------
# envelope_budget_tokens(ctx_size=...) — contract item 1
# ---------------------------------------------------------------------------


class TestEnvelopeBudgetTokensNonRegression:
    def test_bare_call_still_returns_todays_value(self):
        """GREEN today: the zero-arg call must keep behaving identically once
        ``ctx_size`` becomes an optional param — ``triage_condense.py`` calls
        it with zero args and must never notice the signature changed."""
        expected = (
            CONTEXT_TARGET_TOKENS - _AGENT_LOOP_FIXED_TOKENS - _RESPONSE_RESERVE_TOKENS
        )
        assert envelope_budget_tokens() == expected


class TestEnvelopeBudgetTokensCtxSizeParam:
    def test_explicit_none_matches_the_bare_call(self):
        """RED: today's signature takes zero args, so
        ``envelope_budget_tokens(ctx_size=None)`` raises TypeError."""
        assert envelope_budget_tokens(ctx_size=None) == envelope_budget_tokens()

    def test_explicit_ctx_size_is_used_as_the_base_instead_of_context_target(self):
        """RED: an explicit ``ctx_size`` replaces ``CONTEXT_TARGET_TOKENS`` as
        the base of the same fixed-cost subtraction."""
        for ctx in (NPU_CTX_SIZE, GPU_CTX_SIZE):
            expected = ctx - _AGENT_LOOP_FIXED_TOKENS - _RESPONSE_RESERVE_TOKENS
            assert envelope_budget_tokens(ctx_size=ctx) == expected

    def test_npu_profile_yields_a_strictly_smaller_budget_than_gpu(self):
        """RED. This is the invariant the read_tools budget tests lean on:
        a smaller device-profile ctx window must yield a smaller usable
        envelope budget, never an equal or larger one."""
        npu_budget = envelope_budget_tokens(ctx_size=NPU_CTX_SIZE)
        gpu_budget = envelope_budget_tokens(ctx_size=GPU_CTX_SIZE)
        assert npu_budget < gpu_budget

    def test_floors_at_zero_never_negative(self):
        """RED. A ``ctx_size`` too small to cover the fixed agent-loop cost
        plus the response reserve must floor at 0, never go negative."""
        just_under_fixed_cost = _AGENT_LOOP_FIXED_TOKENS + _RESPONSE_RESERVE_TOKENS - 1
        assert envelope_budget_tokens(ctx_size=just_under_fixed_cost) == 0
        assert envelope_budget_tokens(ctx_size=0) == 0


# ---------------------------------------------------------------------------
# active_profile_ctx_size() — contract item 2
# ---------------------------------------------------------------------------


def _patch_default_device(monkeypatch: pytest.MonkeyPatch, device: str) -> None:
    """Monkeypatch ``GaiaConfig.load`` to return a fake config pinned to
    ``device`` — patching the CLASS attribute (not a specific import site)
    means this works whether ``context_budget.py`` imports ``GaiaConfig`` at
    module level or defers the import inside the function body, as long as
    it resolves through ``gaia.config.GaiaConfig.load()`` per the contract.
    """
    fake_config = GaiaConfig(default_device=device)
    monkeypatch.setattr(
        GaiaConfig, "load", classmethod(lambda cls, path=None: fake_config)
    )


class TestActiveProfileCtxSize:
    def test_gpu_profile_resolves_to_gpu_ctx_size(self, monkeypatch):
        """RED: ``active_profile_ctx_size`` does not exist yet."""
        _patch_default_device(monkeypatch, "gpu")
        from gaia_agent_email.context_budget import active_profile_ctx_size

        assert active_profile_ctx_size() == GPU_CTX_SIZE

    def test_cpu_profile_resolves_to_gpu_ctx_size(self, monkeypatch):
        """RED. ``profile_ctx_size`` treats every non-'npu' device as the
        GPU/CPU profile (see lemonade_client.py) — 'cpu' must resolve the
        same as 'gpu', not its own value."""
        _patch_default_device(monkeypatch, "cpu")
        from gaia_agent_email.context_budget import active_profile_ctx_size

        assert active_profile_ctx_size() == GPU_CTX_SIZE

    def test_npu_profile_resolves_to_npu_ctx_size(self, monkeypatch):
        """RED."""
        _patch_default_device(monkeypatch, "npu")
        from gaia_agent_email.context_budget import active_profile_ctx_size

        assert active_profile_ctx_size() == NPU_CTX_SIZE

    def test_smoke_no_monkeypatch_returns_a_valid_profile_ctx_size(self):
        """Smoke test, not a value-pinning test: this test process's real
        HOME/config is out of this suite's control, so only assert the
        result is ONE of the two known profile ctx sizes and that resolving
        it never raises."""
        from gaia_agent_email.context_budget import active_profile_ctx_size

        result = active_profile_ctx_size()
        assert result in (GPU_CTX_SIZE, NPU_CTX_SIZE)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
