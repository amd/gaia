# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which features each model tier gets — the table, not an agent."""

from dataclasses import fields

import pytest

from gaia.agents.base.model_features import (
    COMPACT,
    FEATURE_TOOLS,
    FULL,
    TIER_ENV,
    TIERS,
    ModelFeatures,
    features_for_model,
    withheld_tools,
)
from gaia.llm.lemonade_client import (
    DEFAULT_MODEL_NAME,
    LARGE_DEFAULT_MODEL_NAME,
    MODELS,
    NPU_DEFAULT_MODEL_NAME,
)


@pytest.fixture(autouse=True)
def _follow_the_model(monkeypatch):
    monkeypatch.delenv(TIER_ENV, raising=False)


def test_gemma_gets_the_same_compact_tier_on_the_gpu_and_the_npu():
    assert features_for_model(DEFAULT_MODEL_NAME) is COMPACT
    assert features_for_model(NPU_DEFAULT_MODEL_NAME) is COMPACT


@pytest.mark.parametrize(
    "model_id", [LARGE_DEFAULT_MODEL_NAME, "some-model-gaia-does-not-list", None]
)
def test_larger_and_unlisted_models_get_every_feature(model_id):
    assert features_for_model(model_id) is FULL


def test_the_env_forces_a_tier_for_any_model(monkeypatch):
    monkeypatch.setenv(TIER_ENV, "full")
    assert features_for_model(DEFAULT_MODEL_NAME) is FULL
    monkeypatch.setenv(TIER_ENV, "Compact")
    assert features_for_model(LARGE_DEFAULT_MODEL_NAME) is COMPACT


def test_an_unknown_env_tier_fails_loudly(monkeypatch):
    monkeypatch.setenv(TIER_ENV, "tiny")
    with pytest.raises(ValueError, match="GAIA_FEATURE_TIER='tiny'"):
        features_for_model(DEFAULT_MODEL_NAME)


def test_every_listed_model_names_a_real_tier():
    bad = {k: m.feature_tier for k, m in MODELS.items() if m.feature_tier not in TIERS}
    assert not bad


def test_every_feature_names_its_tools():
    assert set(FEATURE_TOOLS) == {f.name for f in fields(ModelFeatures)}


def test_full_withholds_nothing_and_compact_withholds_every_feature_tool():
    assert withheld_tools(FULL) == frozenset()
    assert withheld_tools(COMPACT) == frozenset().union(*FEATURE_TOOLS.values())
