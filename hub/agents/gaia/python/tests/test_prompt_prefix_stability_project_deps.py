# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Project facts that change mid-session keep each request extending the last.

In a Python project the agent is told which declared dependencies are missing,
and the project map names where it may work without asking. An install changes
the first and a granted folder changes the second, both mid-session. The
dependency line rides in the user turn and the work roots are frozen at the
first render, so neither may change the system prompt.
"""

from __future__ import annotations

import pytest
import test_prompt_prefix_stability as stability
from test_prompt_prefix_stability import flagship  # noqa: F401 - fixture

from gaia.agents.base import project_map


@pytest.fixture
def python_project(monkeypatch, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "proj"\ndependencies = ["asgiref"]\n', encoding="utf-8"
    )
    monkeypatch.setenv(project_map.PROJECT_ROOT_ENV, str(root))
    state = {"missing": ["asgiref"]}
    monkeypatch.setattr(project_map, "missing_deps", lambda names: state["missing"])
    project_map.clear_project_map_cache()
    yield state
    project_map.clear_project_map_cache()


def test_an_install_and_a_grant_mid_session_leave_the_prompt_alone(
    python_project, flagship, tmp_path
):
    agent, model, _doc = flagship
    stability._turn(agent, model, "Why do the tests fail to import?", ["Missing."])

    python_project["missing"] = []
    extra = tmp_path / "extra"
    extra.mkdir()
    agent.path_validator.add_allowed_path(str(extra))
    # Whatever recomposes the prompt next must render the same bytes.
    agent.rebuild_system_prompt()
    stability._turn(agent, model, "I installed it. Try again.", ["They pass."])

    assert len(model.requests) == 2
    system = [request[0][0]["content"] for request in model.requests]
    assert "==== PROJECT MAP ====" in system[0]
    assert "without asking only under:" in system[0]
    assert system[0] == system[1]
    before, after = (stability._render(request) for request in model.requests)
    assert after.startswith(before)

    turn_one, turn_two = (request[0][-1]["content"] for request in model.requests)
    assert "NOT installed for `python`: asgiref (of 1 declared)" in turn_one
    assert "[Python dependencies: all 1 declared are installed.]" in turn_two
