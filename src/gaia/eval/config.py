# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Evaluation framework configuration.

This module contains shared configuration constants used across the evaluation framework.
"""

# Default Claude model for evaluation tasks (the JUDGE, not the model under test).
#
# NOTE ON BASELINES: eval scorecards are scored BY this model, so changing it changes
# what a score means. Baselines committed under a previous judge are not directly
# comparable to runs under this one — regenerate them (`--save-baseline`) and call the
# judge change out explicitly, rather than reading a shifted score as a regression.
DEFAULT_CLAUDE_MODEL = "claude-opus-5"

# Per-million-token pricing for the CLOUD models an eval can run against.
#
# A model absent from this table costs 0.0 — see ``compute_cost``. That is
# right for a locally served model (there is no per-token bill) and wrong for
# a cloud one, where it silently reports a real spend as free. So every cloud
# model the eval can reach has to be listed here, and adding a way to run a new
# cloud provider means adding its rates in the same change.
#
# ``cached_per_mtok`` is optional and, when absent, cached input bills at the
# full input rate. Absent and zero are different offers: zero means the
# provider serves cached prompt tokens for free, and collapsing the two
# misprices every cached turn.
#
# Claude: https://www.anthropic.com/pricing (read 2026-08-04)
# Fireworks: https://docs.fireworks.ai/serverless/pricing (read 2026-09-13)
MODEL_PRICING = {
    # Claude 5 family
    "claude-opus-5": {"input_per_mtok": 5.00, "output_per_mtok": 25.00},
    "claude-sonnet-5": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    "claude-fable-5": {"input_per_mtok": 10.00, "output_per_mtok": 50.00},
    # Claude 4.x family
    "claude-opus-4-8": {"input_per_mtok": 5.00, "output_per_mtok": 25.00},
    "claude-opus-4-7": {"input_per_mtok": 5.00, "output_per_mtok": 25.00},
    "claude-opus-4-6": {"input_per_mtok": 5.00, "output_per_mtok": 25.00},
    "claude-opus-4.1": {"input_per_mtok": 15.00, "output_per_mtok": 75.00},
    "claude-opus-4": {"input_per_mtok": 15.00, "output_per_mtok": 75.00},
    "claude-haiku-4-5": {"input_per_mtok": 1.00, "output_per_mtok": 5.00},
    "claude-haiku-4-5-20251001": {"input_per_mtok": 1.00, "output_per_mtok": 5.00},
    "claude-sonnet-4-6": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    "claude-sonnet-4.5": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    "claude-sonnet-4-5-20250929": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    "claude-sonnet-4": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    "claude-sonnet-4-20250514": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    # Claude 3.x family
    "claude-3-7-sonnet-20250219": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    "claude-3-5-sonnet-20241022": {
        "input_per_mtok": 3.00,
        "output_per_mtok": 15.00,
    },  # deprecated
    "claude-3-5-haiku-20241022": {"input_per_mtok": 0.80, "output_per_mtok": 4.00},
    "claude-3-opus-20240229": {
        "input_per_mtok": 15.00,
        "output_per_mtok": 75.00,
    },  # deprecated
    "claude-3-haiku-20240307": {"input_per_mtok": 0.25, "output_per_mtok": 1.25},
    # Default fallback for unknown models (using Sonnet pricing)
    "default": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    # Fireworks serverless, standard tier. Reached through Lemonade's cloud
    # routing, where the model id carries a "fireworks." prefix.
    #
    # The 5.2 and 5.3 generations are NOT interchangeable: 5.3 charges nearly
    # double for cached input, which is the token class most of a long agent
    # run is made of.
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
