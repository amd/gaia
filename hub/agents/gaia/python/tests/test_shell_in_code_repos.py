# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A session working in a code repository is offered the shell every turn.

Retrieval finds the shell only when a request reads like a shell request. Coding
requests usually don't ("skip gfx90a tests on PRs unless labeled"), so the
flagship worked real repositories with no shell and ran every grep through
``run_python``. The shell is in the flagship's CORE now; the workspace keeps it
there for a profile that drops it.

The embedder here scores every tool at zero, so nothing reaches the model
through retrieval: whatever is offered is CORE plus the workspace.
"""

from __future__ import annotations

import contextlib

import numpy as np
import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig
from gaia_agent.tool_bundles import FULL_CORE_TOOLS

from gaia.agents.base.tools import _TOOL_REGISTRY

CODING_REQUEST = "Skip gfx90a tests on PRs unless labeled"


@contextlib.contextmanager
def _isolated_registry():
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        yield
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _query_vec(self, text):
    return np.array([1.0, 0.0], dtype=np.float32)


def _tool_vecs(self, texts):
    return np.tile(np.array([0.0, 1.0], dtype=np.float32), (len(texts), 1))


def _offered_tools(monkeypatch, env_root: str | None = None, **config) -> list[str]:
    """The native ``tools=`` names a first turn sends the model."""
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    if env_root is None:
        monkeypatch.delenv("GAIA_PROJECT_ROOT", raising=False)
    else:
        monkeypatch.setenv("GAIA_PROJECT_ROOT", env_root)
    monkeypatch.delenv("GAIA_DYNAMIC_TOOLS", raising=False)
    monkeypatch.setattr(GaiaAgent, "_embed_text", _query_vec)
    monkeypatch.setattr(GaiaAgent, "_embed_texts_batch", _tool_vecs)
    monkeypatch.setattr(GaiaAgent, "_uses_native_tool_calls", lambda self: True)
    with _isolated_registry():
        agent = GaiaAgent(
            config=GaiaAgentConfig(silent_mode=True, dynamic_tools=True, **config)
        )
        try:
            # Memory is off (no embedder in CI); the loader only needs a store.
            agent._memory_store = object()
            agent._refresh_active_tool_filter(CODING_REQUEST)
            return [s["function"]["name"] for s in agent._openai_tools]
        finally:
            agent.close()


def _workspace_core(monkeypatch, env_root: str | None = None, **config) -> frozenset:
    """What the workspace adds to CORE, apart from the CORE that has it anyway."""
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    if env_root is None:
        monkeypatch.delenv("GAIA_PROJECT_ROOT", raising=False)
    else:
        monkeypatch.setenv("GAIA_PROJECT_ROOT", env_root)
    with _isolated_registry():
        agent = GaiaAgent(config=GaiaAgentConfig(silent_mode=True, **config))
        try:
            return agent._workspace_core_tools()
        finally:
            agent.close()


def test_a_git_repository_is_offered_the_shell(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()

    names = _offered_tools(monkeypatch, project_root=str(tmp_path))

    assert "run_shell_command" in names


def test_a_repository_found_from_the_working_directory_counts(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    work = tmp_path / "src" / "pkg"
    work.mkdir(parents=True)
    monkeypatch.chdir(work)

    names = _offered_tools(monkeypatch)

    assert "run_shell_command" in names


def test_a_first_turn_stays_on_core_without_a_jump(tmp_path, monkeypatch):
    """A coding request costs nothing extra: no repository, no jump, core alone."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(GaiaAgent, "_project_map_root", lambda self: None)

    names = _offered_tools(monkeypatch)

    assert "execute_python_file" not in names
    assert "run_python" in names
    assert set(names) <= set(FULL_CORE_TOOLS)


@pytest.mark.parametrize("root_source", ["config", "env"])
def test_a_project_root_that_is_not_a_repository_adds_nothing(
    tmp_path, monkeypatch, root_source
):
    monkeypatch.chdir(tmp_path)
    kwargs = (
        {"project_root": str(tmp_path)}
        if root_source == "config"
        else {"env_root": str(tmp_path)}
    )

    assert _workspace_core(monkeypatch, **kwargs) == frozenset()


def test_no_project_at_all_adds_nothing(tmp_path, monkeypatch):
    """The root is stubbed, not walked to.

    Letting the upward walk decide makes the assertion depend on where the
    runner puts ``TMPDIR``: under a checkout it finds that repository and the
    test silently asserts the opposite of its name. ``resolve_project_root``
    has its own tests.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(GaiaAgent, "_project_map_root", lambda self: None)

    assert _workspace_core(monkeypatch) == frozenset()


def test_a_git_repository_adds_the_shell_to_the_workspace_core(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()

    assert _workspace_core(monkeypatch, project_root=str(tmp_path)) == frozenset(
        {"run_shell_command"}
    )
