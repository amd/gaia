# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which chat models can be personalised, and how.

A LoRA only works on the base it was trained against, so each entry pairs the
GGUF Lemonade serves with the Hugging Face checkpoint training reads. The
adapter is trained on the full-precision weights and served on the quantized
GGUF; the eval gate measures it on the GGUF before it is ever used.
"""

from dataclasses import dataclass
from typing import Dict

from gaia.learning.errors import LearningError

# Language-model linear layers only: vision and audio towers stay untouched.
_LANGUAGE_LINEARS = (
    r".*language_model.*\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)$"
)


@dataclass(frozen=True)
class TrainableBase:
    model_name: str  # the Lemonade model id
    hf_repo: str  # full-precision checkpoint training loads
    target_modules: str
    # Peak training memory measured at rank 16, 4-bit, 2K tokens.
    train_memory_gb: float
    attn_implementation: str = "eager"


TRAINABLE_BASES: Dict[str, TrainableBase] = {
    "Gemma-4-E4B-it-GGUF": TrainableBase(
        model_name="Gemma-4-E4B-it-GGUF",
        hf_repo="unsloth/gemma-4-E4B-it",
        target_modules=_LANGUAGE_LINEARS,
        train_memory_gb=24.0,
    ),
}


def trainable_base(model_name: str) -> TrainableBase:
    base = TRAINABLE_BASES.get(model_name)
    if base is None:
        raise LearningError(
            f"Nightly learning can't personalise {model_name!r} yet; it supports "
            f"{sorted(TRAINABLE_BASES)}. Pick one with "
            "`gaia learn config --base-model Gemma-4-E4B-it-GGUF`."
        )
    return base
