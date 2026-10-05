# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""With image generation off, each request still extends the one before it.

The system prompt's image-generation rule follows ``enable_sd_tools``. The
flagship turns it on, so ``test_prompt_prefix_stability`` covers that rule;
this covers the "not available" rule a session without it gets.
"""

from __future__ import annotations

import functools

import pytest
import test_prompt_prefix_stability as stability
from test_prompt_prefix_stability import flagship  # noqa: F401 - fixture


@pytest.fixture
def sd_off(monkeypatch):
    monkeypatch.setattr(
        stability,
        "GaiaAgentConfig",
        functools.partial(stability.GaiaAgentConfig, enable_sd_tools=False),
    )


def test_prompt_without_image_generation_stays_fixed(sd_off, flagship):
    agent, model, _doc = flagship
    stability._turn(agent, model, "Hi, I'm Sam.", ["Hi Sam."])
    stability._turn(agent, model, "What can you help me with?", ["Plenty."])

    assert len(model.requests) == 2
    system = [request[0][0]["content"] for request in model.requests]
    assert "**IMAGE GENERATION:** Not available in this session." in system[0]
    assert system[0] == system[1]
    before, after = (stability._render(request) for request in model.requests)
    assert after.startswith(before)
