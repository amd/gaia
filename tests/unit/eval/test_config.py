# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for the eval framework's shared configuration.

These pin the judge model and the pricing table it depends on. The judge is the
model that SCORES eval runs, so changing it changes what a score means — the
assertion below is a deliberate tripwire, not busywork: if you bump the judge,
update it here and regenerate the committed baselines in the same change.
"""

from gaia.eval.config import DEFAULT_CLAUDE_MODEL, MODEL_PRICING


class TestJudgeModel:
    def test_default_judge_is_opus_5(self):
        """Deliberate tripwire — see the module docstring before changing this."""
        assert DEFAULT_CLAUDE_MODEL == "claude-opus-5"

    def test_runner_default_matches_config(self):
        """The runner must not drift from the shared config default."""
        from gaia.eval.runner import DEFAULT_MODEL

        assert DEFAULT_MODEL == DEFAULT_CLAUDE_MODEL


class TestModelPricing:
    def test_default_judge_is_priced(self):
        """An unpriced judge silently bills at the 'default' row and misreports cost.

        MODEL_PRICING has a `default` fallback, so a missing entry does not raise —
        it quietly reports Sonnet rates for whatever model actually ran. That is the
        silent-fallback shape CLAUDE.md forbids, and it would understate the cost of
        an Opus-tier judge by ~1.7x. Keep the active judge explicitly priced.
        """
        assert DEFAULT_CLAUDE_MODEL in MODEL_PRICING

    def test_priced_judge_uses_opus_tier_rates(self):
        pricing = MODEL_PRICING[DEFAULT_CLAUDE_MODEL]
        assert pricing["input_per_mtok"] == 5.00
        assert pricing["output_per_mtok"] == 25.00

    def test_every_entry_has_both_rates(self):
        for model, pricing in MODEL_PRICING.items():
            assert "input_per_mtok" in pricing, f"{model} missing input rate"
            assert "output_per_mtok" in pricing, f"{model} missing output rate"
            assert pricing["input_per_mtok"] >= 0
            assert pricing["output_per_mtok"] >= 0
