# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``execute_python_file`` must let a project's test file import the project.

It used to run every script from the script's own folder, so
``tests/test_dates.py`` doing ``from toybox import dates`` failed with
``ModuleNotFoundError`` — the project root was neither the cwd nor on the
import path — and the agent retried the identical call until the loop guard
ended the run.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat.agent import (  # noqa: E402
    ChatAgent,
    ChatAgentConfig,
    python_script_run_context,
)

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402


@pytest.fixture
def project(tmp_path):
    """A tiny project: ``pkg/mod.py`` and a test file that imports it."""
    root = tmp_path / "project"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "mod.py").write_text("VALUE = 42\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text(
        "import os\n"
        "from pkg import mod\n"
        "print('value', mod.VALUE)\n"
        "print('cwd', os.getcwd())\n",
        encoding="utf-8",
    )
    return root.resolve()


@pytest.fixture
def execute_python_file(project, monkeypatch):
    monkeypatch.chdir(project)
    saved = dict(_TOOL_REGISTRY)
    try:
        config = ChatAgentConfig(silent_mode=True, allowed_paths=[str(project)])
        with (
            patch("gaia_agent_chat.agent.RAGSDK"),
            patch("gaia_agent_chat.agent.RAGConfig"),
        ):
            ChatAgent(config)
        entry = _TOOL_REGISTRY.get("execute_python_file")
        assert entry is not None, "execute_python_file was not registered"
        yield entry["function"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


class TestTheToolRunsProjectTests:
    def test_a_test_file_can_import_the_project(self, execute_python_file, project):
        result = execute_python_file(file_path=str(project / "tests" / "test_x.py"))

        assert result["status"] == "success", result
        assert result["return_code"] == 0, result["stderr"]
        assert "value 42" in result["stdout"]
        assert f"cwd {project}" in result["stdout"]


class TestRunContext:
    def test_a_script_under_the_project_runs_from_the_project(self, project):
        run_dir, env = python_script_run_context(
            project / "tests" / "test_x.py", project
        )

        assert run_dir == project
        assert env["PYTHONPATH"].split(os.pathsep)[0] == str(project)

    def test_an_existing_pythonpath_is_kept(self, project, monkeypatch):
        monkeypatch.setenv("PYTHONPATH", "/somewhere/else")

        _, env = python_script_run_context(project / "tests" / "test_x.py", project)

        assert env["PYTHONPATH"] == os.pathsep.join([str(project), "/somewhere/else"])

    def test_a_script_outside_the_project_runs_from_its_own_folder(
        self, project, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("PYTHONPATH", "/untouched")
        outside = tmp_path / "elsewhere" / "script.py"
        outside.parent.mkdir()
        outside.write_text("print('hi')\n", encoding="utf-8")

        run_dir, env = python_script_run_context(outside, project)

        assert run_dir == outside.parent.resolve()
        assert env["PYTHONPATH"] == "/untouched"
