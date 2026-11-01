# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""LFM3 1.7B as an optional local model: registration, KV sizing and fit."""

import pytest

from gaia.llm import lemonade_client as lc
from gaia.llm import model_fit
from gaia.llm.model_fit import capacity_from_system_info

from .test_model_fit import CPU_ONLY, DGPU, MAC_M4, STRIX_HALO_64, STRIX_HALO_128

LFM3 = lc.find_model_requirement(lc.LFM3_MODEL_NAME)


def test_registers_as_a_user_model_from_liquids_gguf():
    assert LFM3.model_id == "user.LFM3-1.7B-GGUF"
    assert LFM3.pull_kwargs()["checkpoint"] == "LiquidAI/LFM3-1.7B-GGUF:Q4_K_M"
    assert LFM3.pull_kwargs()["recipe"] == "llamacpp"
    assert LFM3.tool_calling and lc.is_tool_calling_model(LFM3.model_id)


def test_kv_cache_is_its_six_full_attention_layers():
    layers, kv_heads, head_dim, k_and_v, f16 = 6, 4, 128, 2, 2
    assert LFM3.kv_bytes_per_token == layers * kv_heads * head_dim * k_and_v * f16
    assert LFM3.max_ctx_size == 262144
    assert LFM3.min_ctx_size == lc.GPU_CTX_SIZE


def test_optional_and_never_a_default():
    assert lc.LFM3_MODEL_NAME in lc.OPTIONAL_CHAT_MODELS
    assert lc.LFM3_MODEL_NAME not in lc.DEFAULT_MODEL_LADDER


@pytest.mark.parametrize(
    "info", [STRIX_HALO_128, STRIX_HALO_64, DGPU, CPU_ONLY, MAC_M4]
)
def test_offered_on_every_machine_including_16gb_ones(info):
    fits = lc.optional_chat_models_that_fit(capacity_from_system_info(info))
    assert lc.LFM3_MODEL_NAME in fits


def test_needs_under_3gb_at_its_64k_floor():
    kv = model_fit.kv_cache_gb(LFM3.kv_bytes_per_token, LFM3.min_ctx_size)
    assert model_fit.required_memory_gb(LFM3.size_gb, kv) < 3
