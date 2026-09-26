# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``delegate_task`` on the flagship: off by default, always offered when on,
and never present on the child it spawns."""

# pylint: disable=protected-access,unused-argument,unidiomatic-typecheck,no-member

from __future__ import annotations

import contextlib

import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

from gaia.agents.base.tools import _TOOL_REGISTRY


@contextlib.contextmanager
def _isolated_registry():
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        yield
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon"))
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    monkeypatch.setenv("GAIA_PROJECT_MAP_AUTO_INDEX", "0")
    monkeypatch.delenv("GAIA_DELEGATE", raising=False)
    monkeypatch.delenv("GAIA_DELEGATE_MAX_STEPS", raising=False)
    return monkeypatch


def _agent(**fields) -> GaiaAgent:
    return GaiaAgent(config=GaiaAgentConfig(silent_mode=True, **fields))


def test_off_by_default_and_on_by_env(env):
    with _isolated_registry():
        assert "delegate_task" not in _agent()._tools_registry
    env.setenv("GAIA_DELEGATE", "1")
    with _isolated_registry():
        agent = _agent()
        assert "delegate_task" in agent._tools_registry
        assert "delegate_task" in agent.tool_loader._core


def test_child_matches_parent_but_cannot_delegate(env):
    env.setenv("GAIA_DELEGATE_MAX_STEPS", "7")
    with _isolated_registry():
        parent = _agent(delegate_enabled=True, max_steps=30)
        parent.console.auto_approve_gated_tools = True
        parent.console.full_access = True
        child = parent._spawn_child()
        try:
            assert type(child) is GaiaAgent
            assert child.config.delegate_depth == 1
            assert child.config.model_id == parent.config.model_id
            assert child.config.allowed_paths == parent.config.allowed_paths
            assert child.max_steps == 7
            assert child.silent_mode is True
            assert child.console.auto_approve_gated_tools is True
            assert child.console.full_access is True
            assert child.conversation_history == []
            assert "delegate_task" not in child._tools_registry
            assert "delegate_task" not in child.tool_loader._core
            assert child.system_prompt == parent.system_prompt
        finally:
            child.close()
        parent.close()


def test_agent_built_after_a_delegating_one_stays_clean(env):
    """The process-global registry must not leak the tool into a later agent."""
    with _isolated_registry():
        on = _agent(delegate_enabled=True)
        off = _agent()
        assert "delegate_task" in on._tools_registry
        assert "delegate_task" not in off._tools_registry
        off.close()
        on.close()
