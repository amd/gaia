# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``delegate_task`` on the flagship: off by default, always offered in ``tool``
mode, the only tool besides ``read_tool_output`` in ``orchestrate`` mode, and
never present on the child it spawns."""

# pylint: disable=protected-access,unused-argument,unidiomatic-typecheck,no-member

from __future__ import annotations

import contextlib
import subprocess

import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.delegate_tools import (
    DELEGATE_SYSTEM_PROMPT,
    ORCHESTRATE_SYSTEM_PROMPT,
    ORCHESTRATOR_TOOLS,
)


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
    monkeypatch.delenv("GAIA_DELEGATE_MAX_CHILDREN", raising=False)
    monkeypatch.delenv("GAIA_PROJECT_ROOT", raising=False)
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
        parent = _agent(delegate_mode="tool", max_steps=30)
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
            assert DELEGATE_SYSTEM_PROMPT in parent.system_prompt
            assert DELEGATE_SYSTEM_PROMPT not in child.system_prompt
            assert child.system_prompt == parent.system_prompt.replace(
                DELEGATE_SYSTEM_PROMPT + "\n\n", ""
            )
        finally:
            child.close()
        parent.close()


def test_prompt_is_byte_identical_when_delegation_is_off(env):
    with _isolated_registry():
        off = _agent()
        baseline = off.system_prompt
        off.close()
    with _isolated_registry():
        on = _agent(delegate_mode="tool")
        assert DELEGATE_SYSTEM_PROMPT in on.system_prompt
        on.close()
    with _isolated_registry():
        again = _agent()
        assert again.system_prompt == baseline
        assert DELEGATE_SYSTEM_PROMPT not in baseline
        again.close()


def test_agent_built_after_a_delegating_one_stays_clean(env):
    """The process-global registry must not leak the tool into a later agent."""
    with _isolated_registry():
        on = _agent(delegate_mode="tool")
        off = _agent()
        assert "delegate_task" in on._tools_registry
        assert "delegate_task" not in off._tools_registry
        off.close()
        on.close()


# ── orchestrate mode ─────────────────────────────────────────────────────────


@pytest.fixture
def code_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "pkg.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root,
        check=True,
    )
    return root


def _offered(agent: GaiaAgent, user_input: str) -> list:
    """The tool names the model is offered on a turn for *user_input*."""
    agent._refresh_active_tool_filter(user_input)
    if agent._active_tool_filter is None:
        return sorted(agent._tools_registry)
    schemas = agent._build_openai_tool_schemas(filter_to=agent._active_tool_filter)
    names = [s["function"]["name"] for s in schemas]
    assert names == agent._active_tool_filter
    return names


_CODE_TURN = "fix the failing test in tests/test_pkg.py and make pytest pass"
_PLAIN_TURN = "what is the capital of France?"


def test_orchestrate_offers_exactly_the_orchestrator_set(env, code_repo, tmp_path):
    env.setenv("GAIA_DELEGATE", "orchestrate")
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    for root in (str(code_repo), str(plain_dir)):
        env.setenv("GAIA_PROJECT_ROOT", root)
        with _isolated_registry():
            agent = _agent()
            assert agent._resolve_delegate_mode() == "orchestrate"
            assert agent.tool_loader is None
            assert "load_tools" not in agent._tools_registry
            for turn in (_CODE_TURN, _PLAIN_TURN):
                assert _offered(agent, turn) == list(ORCHESTRATOR_TOOLS), (root, turn)
            assert ORCHESTRATE_SYSTEM_PROMPT in agent.system_prompt
            assert DELEGATE_SYSTEM_PROMPT not in agent.system_prompt
            refused = agent._execute_tool("read_file", {"file_path": "pkg.py"})
            assert refused["status"] == "error"
            assert refused["executed"] is False
            assert "delegate_task and read_tool_output" in refused["error"]
            agent.close()


def test_orchestrating_parent_spawns_a_fully_equipped_child(env, code_repo):
    env.setenv("GAIA_PROJECT_ROOT", str(code_repo))
    with _isolated_registry():
        plain = _agent()
        full_set = sorted(plain._tools_registry)
        plain.close()
    env.setenv("GAIA_DELEGATE", "orchestrate")
    with _isolated_registry():
        parent = _agent()
        parent.console.auto_approve_gated_tools = True
        child = parent._spawn_child()
        try:
            assert child.config.delegate_depth == 1
            assert child._resolve_delegate_mode() == "off"
            assert child.tool_loader is not None
            assert sorted(child._tools_registry) == full_set
            assert "delegate_task" not in child._tools_registry
            assert _offered(child, _CODE_TURN) == full_set
            assert ORCHESTRATE_SYSTEM_PROMPT not in child.system_prompt
            assert DELEGATE_SYSTEM_PROMPT not in child.system_prompt
            assert child._execute_tool("nonexistent_tool", {})["status"] == "error"
            assert child._orchestrator_refusal("read_file") is None
        finally:
            child.close()
        parent.close()


def test_tool_mode_offered_set_is_the_plain_set_plus_delegate_task(env, code_repo):
    """Pins ``tool`` mode: everything the plain agent offers, plus the one tool."""
    env.setenv("GAIA_PROJECT_ROOT", str(code_repo))
    with _isolated_registry():
        plain = _agent()
        plain_offered = _offered(plain, _CODE_TURN)
        plain.close()
    env.setenv("GAIA_DELEGATE", "1")
    with _isolated_registry():
        agent = _agent()
        assert agent._resolve_delegate_mode() == "tool"
        assert agent.tool_loader is not None
        assert "delegate_task" in agent.tool_loader._core
        for turn in (_CODE_TURN, _PLAIN_TURN):
            assert _offered(agent, turn) == sorted(plain_offered + ["delegate_task"])
        assert DELEGATE_SYSTEM_PROMPT in agent.system_prompt
        assert ORCHESTRATE_SYSTEM_PROMPT not in agent.system_prompt
        assert agent._orchestrator_refusal("read_file") is None
        agent.close()


def test_malformed_mode_fails_at_construction(env):
    env.setenv("GAIA_DELEGATE", "sometimes")
    with _isolated_registry():
        with pytest.raises(ValueError, match="GAIA_DELEGATE must be one of"):
            _agent()
    env.delenv("GAIA_DELEGATE")
    with _isolated_registry():
        with pytest.raises(ValueError, match="delegate_mode must be one of"):
            _agent(delegate_mode="banana")
