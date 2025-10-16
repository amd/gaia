# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Evaluation framework configuration.

This module contains shared configuration constants used across the evaluation framework.
"""

# Default Claude model for evaluation tasks
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-5-20250929"

# Claude API pricing (per million tokens) - based on https://www.anthropic.com/pricing
# Last updated: 2025-10-01
MODEL_PRICING = {
    # Claude 4.x family
    "claude-opus-4.1": {"input_per_mtok": 15.00, "output_per_mtok": 75.00},
    "claude-opus-4": {"input_per_mtok": 15.00, "output_per_mtok": 75.00},
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
