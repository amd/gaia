# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The project map follows the folder the client was started in (#3591).

The daemon starts the sidecar in its own folder, so the process cwd never names
the user's project. The terminal app sends its start folder as /query's
``working_directory``; the map, run_python's project and lesson keys must
resolve from it, and the file scope must stay what it was without it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent")


@pytest.fixture
def build(monkeypatch, tmp_path):
    from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

    from gaia.agents.base.tools import _TOOL_REGISTRY

    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_MEMORY_DB", str(tmp_path / "home" / "memory.db"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon"))
    monkeypatch.setenv("GAIA_PROJECT_MAP_AUTO_INDEX", "0")
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9/api/v1")
    monkeypatch.delenv("GAIA_ALLOWED_PATHS", raising=False)
    monkeypatch.delenv("GAIA_PROJECT_ROOT", raising=False)
    sidecar = tmp_path / "sidecar"
    sidecar.mkdir()
    monkeypatch.chdir(sidecar)
    saved = dict(_TOOL_REGISTRY)
    agents = []

    def make(**kw):
        _TOOL_REGISTRY.clear()
        agents.append(GaiaAgent(config=GaiaAgentConfig(silent_mode=True, **kw)))
        return agents[-1]

    try:
        yield make
    finally:
        for agent in agents:
            agent.close()
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (root / "src" / "demo").mkdir(parents=True)
    return root


def test_the_map_is_of_the_start_folders_project(build, project):
    agent = build(working_directory=str(project / "src" / "demo"))

    assert agent._project_map_root() == str(project.resolve())
    assert agent._script_project_root() == str(project.resolve())
    prompt = agent._compose_system_prompt()
    assert "PROJECT MAP" in prompt and str(project.resolve()) in prompt


def test_the_start_folder_does_not_change_the_file_scope(build, project):
    without = build()
    with_start = build(working_directory=str(project))

    assert sorted(map(str, with_start.path_validator.allowed_paths)) == sorted(
        map(str, without.path_validator.allowed_paths)
    )


def test_without_a_start_folder_the_daemons_folder_maps_nothing(build):
    assert build()._project_map_root() is None
