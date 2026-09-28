# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The SD prompt text must agree with the parameters the SD code really uses.

``gaia.sd.prompts`` tells the model which size/steps/cfg_scale to pass to
``generate_image`` for each SD model. If that text drifts from
``LemonadeClient.SD_MODEL_DEFAULTS`` the model is steered to settings the
defaults were tuned away from, and nothing else would notice.
"""

import re
from unittest.mock import patch

import pytest

from gaia.agents.base import tools as tool_registry
from gaia.llm.lemonade_client import LemonadeClient
from gaia.sd import SDToolsMixin
from gaia.sd.prompts import (
    BASE_GUIDELINES,
    MODEL_SPECIFIC_PROMPTS,
    WORKFLOW_INSTRUCTIONS,
)

_CALL_INSTRUCTION = re.compile(
    r'generate_image with model="(?P<model>[^"]+)", size="(?P<size>[^"]+)", '
    r"steps=(?P<steps>\d+), cfg_scale=(?P<cfg>[\d.]+)"
)


def _call_instruction(model: str) -> dict:
    matches = list(_CALL_INSTRUCTION.finditer(MODEL_SPECIFIC_PROMPTS[model]))
    assert len(matches) == 1, f"{model}: expected one generate_image instruction"
    return matches[0].groupdict()


def test_every_sd_model_has_its_own_prompt_section():
    assert set(MODEL_SPECIFIC_PROMPTS) == set(LemonadeClient.SD_MODELS)


@pytest.mark.parametrize("model", LemonadeClient.SD_MODELS)
def test_prompt_tells_the_model_to_call_with_the_real_defaults(model):
    defaults = LemonadeClient.SD_MODEL_DEFAULTS[model]
    call = _call_instruction(model)

    assert call["model"] == model
    assert call["size"] == defaults["size"]
    assert int(call["steps"]) == defaults["steps"]
    assert float(call["cfg"]) == defaults["cfg_scale"]


@pytest.mark.parametrize("model", LemonadeClient.SD_MODELS)
def test_system_prompt_carries_only_the_configured_models_section(model):
    mixin = SDToolsMixin()
    mixin.sd_default_model = model

    prompt = mixin.get_sd_system_prompt()

    assert prompt.startswith(BASE_GUIDELINES)
    assert WORKFLOW_INSTRUCTIONS in prompt
    assert prompt.endswith(MODEL_SPECIFIC_PROMPTS[model])
    for other in set(LemonadeClient.SD_MODELS) - {model}:
        assert MODEL_SPECIFIC_PROMPTS[other] not in prompt


def test_system_prompt_before_init_sd_has_no_model_section():
    prompt = SDToolsMixin().get_sd_system_prompt()

    assert prompt == BASE_GUIDELINES + WORKFLOW_INSTRUCTIONS


@pytest.fixture
def restore_tool_registry():
    saved = dict(tool_registry._TOOL_REGISTRY)
    yield
    tool_registry._TOOL_REGISTRY.clear()
    tool_registry._TOOL_REGISTRY.update(saved)


def test_list_sd_models_tool_reports_the_real_defaults(tmp_path, restore_tool_registry):
    with patch("gaia.sd.mixin.LemonadeClient"):
        SDToolsMixin().init_sd(output_dir=str(tmp_path))

    listed = tool_registry._TOOL_REGISTRY["list_sd_models"]["function"]()["models"]

    assert {m["name"] for m in listed} == set(LemonadeClient.SD_MODELS)
    for entry in listed:
        defaults = LemonadeClient.SD_MODEL_DEFAULTS[entry["name"]]
        assert entry["recommended_size"] == defaults["size"], entry["name"]
        assert entry["recommended_steps"] == defaults["steps"], entry["name"]
