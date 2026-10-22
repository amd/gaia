# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A cloud model's context window is the provider's, never Lemonade's 4096.

Lemonade v2026.42.1 lists a cloud model it has "loaded" with
``context_length: 4096`` and its real window in ``max_context_window``. The
payloads below are the ones captured from that Lemonade.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gaia.agents.base import history
from gaia.agents.base.agent import Agent
from gaia.llm.gateway import GatewayManager
from gaia.llm.lemonade_client import (
    GPU_CTX_SIZE,
    LemonadeClient,
    budget_for_ctx,
    catalog_context_window,
    cloud_model_info,
    record_cloud_models,
    truncation_budget,
)
from gaia.ui import turn_insights

CLOUD = "fireworks.deepseek-v4p1-flash"
WINDOW = 1048576

# The model in use: Lemonade "loaded" it, so context_length fell to 4096.
USED = {
    "id": CLOUD,
    "recipe": "cloud",
    "labels": ["tool-calling"],
    "context_length": 4096,
    "max_context_window": WINDOW,
}
# A variant never used: both fields carry the real window.
UNUSED = {
    "id": "fireworks.deepseek-v4p1-flash-spot",
    "recipe": "cloud",
    "labels": ["tool-calling"],
    "context_length": WINDOW,
    "max_context_window": WINDOW,
}
# A local model's context_length is the window it is configured at.
LOCAL = {
    "id": "Gemma-4-E4B-it-GGUF",
    "recipe": "llamacpp",
    "labels": [],
    "downloaded": True,
    "context_length": 65536,
    "max_context_window": 131072,
}


@pytest.fixture(autouse=True)
def cold_cache():
    record_cloud_models({"data": []})
    yield
    record_cloud_models({"data": []})


@pytest.mark.parametrize(
    "entry,expected",
    [
        (USED, WINDOW),
        (UNUSED, WINDOW),
        (LOCAL, 65536),
        # Older Lemonade lists only context_length, which is then the real window.
        ({"id": "amd.x", "recipe": "cloud", "context_length": 200000}, 200000),
        ({"id": "amd.x", "recipe": "cloud"}, None),
        ({"id": "local", "recipe": "llamacpp", "context_length": 0}, None),
    ],
)
def test_catalog_window(entry, expected):
    assert catalog_context_window(entry) == expected


def test_the_cloud_catalog_remembers_the_providers_window():
    record_cloud_models({"data": [USED, UNUSED, LOCAL]})
    assert cloud_model_info(CLOUD)["ctx_size"] == WINDOW
    assert cloud_model_info(UNUSED["id"])["ctx_size"] == WINDOW


def test_the_context_meter_shows_the_providers_window():
    record_cloud_models({"data": [USED]})
    agent = SimpleNamespace(
        model_id=CLOUD,
        system_prompt="You are GAIA.",
        _openai_tools=[],
        chat=SimpleNamespace(config=SimpleNamespace(max_tokens=1024)),
    )
    usage = turn_insights.measure_context(agent, [], CLOUD)
    assert usage["model_window"] == WINDOW
    # GAIA's own admission budget for a cloud turn is unchanged.
    assert usage["window"] == GPU_CTX_SIZE


def test_gateway_models_carry_the_providers_window():
    gateway_model = {**USED, "id": "amd.Claude-Opus-5"}
    client = MagicMock()
    client.list_models.return_value = {"data": [gateway_model]}
    (model,) = GatewayManager(client=client).list_models()
    assert model.ctx_size == WINDOW


def test_a_cloud_turn_is_never_budgeted_from_lemonades_4096():
    """History, reply reserve and tool-result truncation ignore the catalog."""
    record_cloud_models({"data": [USED]})
    backend = MagicMock(spec=LemonadeClient)
    backend.cloud_model_provider.return_value = "fireworks"
    agent = SimpleNamespace(
        model_id=CLOUD,
        device=None,
        system_prompt="You are GAIA.",
        _openai_tools=[],
        chat=SimpleNamespace(
            llm_client=SimpleNamespace(_backend=backend),
            config=SimpleNamespace(max_tokens=8192),
        ),
    )
    assert history.context_window(agent) == GPU_CTX_SIZE
    assert history.history_budget(agent, "hi") > 4096 * 4
    assert Agent._truncation_budget(agent) == truncation_budget(None)
    assert Agent._truncation_budget(agent) != budget_for_ctx(4096)
