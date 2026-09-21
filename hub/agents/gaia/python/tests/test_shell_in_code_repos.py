# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A session working in a code repository is offered the shell every turn.

Semantic tool selection admits the shell only when a request reads like a shell
request. Coding requests usually don't ("skip gfx90a tests on PRs unless
labeled"), so the flagship worked real repositories with no shell and ran every
grep through ``run_python``. Availability now follows the workspace instead.

The embedder here scores every tool at zero, so nothing reaches the model
through semantic matching: whatever is offered is CORE plus the workspace.
"""

from __future__ import annotations

import contextlib

import numpy as np
import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

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


def _offered_tools(monkeypatch, **config) -> list[str]:
    """The native ``tools=`` names a first turn sends the model."""
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    monkeypatch.delenv("GAIA_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("GAIA_DYNAMIC_TOOLS", raising=False)
    monkeypatch.setattr(GaiaAgent, "_embed_text", _query_vec)
    monkeypatch.setattr(GaiaAgent, "_embed_texts_batch", _tool_vecs)
    monkeypatch.setattr(GaiaAgent, "_uses_native_tool_calls", lambda self: True)
    with _isolated_registry():
        agent = GaiaAgent(config=GaiaAgentConfig(silent_mode=True, **config))
        try:
            # Memory is off (no embedder in CI); the loader only needs a store.
            agent._memory_store = object()
            agent._refresh_active_tool_filter(CODING_REQUEST)
            return [s["function"]["name"] for s in agent._openai_tools]
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


@pytest.mark.parametrize("explicit_root", [True, False])
def test_outside_a_repository_the_shell_still_waits_for_the_request(
    tmp_path, monkeypatch, explicit_root
):
    monkeypatch.chdir(tmp_path)
    config = {"project_root": str(tmp_path)} if explicit_root else {}

    names = _offered_tools(monkeypatch, **config)

    assert "run_shell_command" not in names
    # The selection ran — this is not the full-registry fallback.
    assert "run_python" in names
    assert "execute_python_file" not in names
