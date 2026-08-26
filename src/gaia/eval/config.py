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

# Claude API pricing (per million tokens) - based on https://www.anthropic.com/pricing
# Last updated: 2026-08-04
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
}
