# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Context eviction on the flagship: off by default, on through the config."""

# pylint: disable=protected-access

import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

from gaia.agents.base.context_eviction import CONTEXT_EVICTION_ENV_VAR


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon"))
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    monkeypatch.setenv("GAIA_PROJECT_MAP_AUTO_INDEX", "0")
    monkeypatch.delenv(CONTEXT_EVICTION_ENV_VAR, raising=False)
    return monkeypatch


def test_off_by_default_and_on_through_the_config(env):
    assert (
        GaiaAgent(config=GaiaAgentConfig(silent_mode=True)).context_eviction_enabled
        is False
    )

    agent = GaiaAgent(
        config=GaiaAgentConfig(
            silent_mode=True, context_eviction="on", context_eviction_keep_steps=3
        )
    )
    assert agent.context_eviction_enabled is True
    assert agent._context_evictor.keep_steps == 3

    env.setenv(CONTEXT_EVICTION_ENV_VAR, "off")
    assert (
        GaiaAgent(
            config=GaiaAgentConfig(silent_mode=True, context_eviction="on")
        ).context_eviction_enabled
        is False
    )
