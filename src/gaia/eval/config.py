# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Evaluation framework configuration.

This module contains shared configuration constants used across the evaluation framework.
"""

from gaia.llm.pricing import ANTHROPIC_PRICES

# Default Claude model for evaluation tasks (the JUDGE, not the model under test).
#
# NOTE ON BASELINES: eval scorecards are scored BY this model, so changing it changes
# what a score means. Baselines committed under a previous judge are not directly
# comparable to runs under this one — replace them from the next nightly on the Strix
# Halo pool and call the judge change out explicitly, rather than reading a shifted
# score as a regression.
DEFAULT_CLAUDE_MODEL = "claude-opus-5"

# The agent under test. Every scenario scores the flagship — the agent users
# actually meet — so a scorecard names one agent and comparing two is valid.
DEFAULT_AGENT_TYPE = "gaia"

# Per-million-token pricing for cloud models. An absent model costs 0.0 (local), so
# every reachable cloud model must be listed or its spend reads as free.
# ``cached_per_mtok`` absent = cached input bills at the input rate; 0 = free.
# Fireworks: https://docs.fireworks.ai/serverless/pricing (read 2026-09-13)
MODEL_PRICING = {
    # Claude list prices: the runtime's table (gaia.llm.pricing).
    **ANTHROPIC_PRICES,
    # Fireworks serverless via Lemonade cloud routing. 5.3 cached input costs ~2x 5.2's.
    "fireworks.glm-5p2": {
        "input_per_mtok": 1.40,
        "output_per_mtok": 4.40,
        "cached_per_mtok": 0.14,
    },
    "fireworks.accounts/fireworks/routers/glm-5p2-fast": {
        "input_per_mtok": 2.10,
        "output_per_mtok": 6.60,
        "cached_per_mtok": 0.21,
    },
    "fireworks.glm-5p3": {
        "input_per_mtok": 1.40,
        "output_per_mtok": 4.40,
        "cached_per_mtok": 0.26,
    },
    "fireworks.glm-5p3-flash": {
        "input_per_mtok": 0.15,
        "output_per_mtok": 0.50,
        "cached_per_mtok": 0.03,
    },
    "fireworks.accounts/fireworks/routers/glm-5p3-fast": {
        "input_per_mtok": 2.10,
        "output_per_mtok": 6.60,
        "cached_per_mtok": 0.39,
    },
}


def claude_pricing(model: str) -> dict:
    """The list price row for a Claude *model*, or an error naming where to add it."""
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        raise ValueError(
            f"No price for Claude model {model!r}, so its eval cost can't be "
            "reported. Add its list price (https://www.anthropic.com/pricing) to "
            "ANTHROPIC_PRICES in src/gaia/llm/pricing.py."
        )
    return pricing
