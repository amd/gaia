# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``execute_python_file`` must let a project's test file import the project.

It used to run every script from the script's own folder, so
``tests/test_dates.py`` doing ``from toybox import dates`` failed with
``ModuleNotFoundError`` — the project root was neither the cwd nor on the
import path — and the agent retried the identical call until the loop guard
ended the run.

Outside a project there is nothing to put on the path, and guessing costs more
than it buys: the script runs from its own folder with the environment
untouched, exactly as before.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat.agent import (  # noqa: E402
    ChatAgent,
    ChatAgentConfig,
    _python_script_run_context,
)

from gaia.agents.base.project_map import PROJECT_ROOT_ENV  # noqa: E402
from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402


@pytest.fixture(autouse=True)
def no_inherited_project_root(monkeypatch):
    """The developer's own ``GAIA_PROJECT_ROOT`` must not steer these tests."""
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)


@pytest.fixture
def project(tmp_path):
    """A tiny project: ``pkg/mod.py`` and a test file that imports it."""
    root = tmp_path / "project"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "mod.py").write_text("VALUE = 42\n", encoding="utf-8")
    # A manifest at the root is what makes this a project to resolve_project_root.
    (root / "pyproject.toml").write_text("[project]\nname = 'toy'\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text(
        "import os\n"
        "from pkg import mod\n"
        "print('value', mod.VALUE)\n"
        "print('cwd', os.getcwd())\n",
        encoding="utf-8",
    )
    return root.resolve()


def _build_agent(config):
    with (
        patch("gaia_agent_chat.agent.RAGSDK"),
        patch("gaia_agent_chat.agent.RAGConfig"),
    ):
        return ChatAgent(config)


@pytest.fixture
def make_execute_python_file(project):
    """Build the agent from a chosen cwd and hand back its registered tool."""
    saved = dict(_TOOL_REGISTRY)

    def build(allowed_path=None):
        config = ChatAgentConfig(
            silent_mode=True, allowed_paths=[str(allowed_path or project)]
        )
        _build_agent(config)
        entry = _TOOL_REGISTRY.get("execute_python_file")
        assert entry is not None, "execute_python_file was not registered"
        return entry["function"]

    try:
        yield build
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


class TestTheToolRunsProjectTests:
    def test_a_test_file_can_import_the_project(
        self, make_execute_python_file, project, monkeypatch
    ):
        monkeypatch.chdir(project)
        execute_python_file = make_execute_python_file()

        result = execute_python_file(file_path=str(project / "tests" / "test_x.py"))

        assert result["status"] == "success", result
        assert result["return_code"] == 0, result["stderr"]
        assert "value 42" in result["stdout"]
        assert f"cwd {project}" in result["stdout"]

    def test_it_works_when_launched_from_a_subdirectory(
        self, make_execute_python_file, project, monkeypatch
    ):
        """Launching from ``project/tests`` must still resolve to ``project``."""
        monkeypatch.chdir(project / "tests")
        execute_python_file = make_execute_python_file()

        result = execute_python_file(file_path=str(project / "tests" / "test_x.py"))

        assert result["status"] == "success", result
        assert result["return_code"] == 0, result["stderr"]
        assert "value 42" in result["stdout"]
        assert f"cwd {project}" in result["stdout"]

    def test_an_unusable_project_root_is_a_tool_error_not_a_crash(
        self, make_execute_python_file, project, monkeypatch, tmp_path
    ):
        """``resolve_project_root`` rejects a non-directory — the tool reports it."""
        monkeypatch.chdir(project)
        monkeypatch.setenv(PROJECT_ROOT_ENV, str(project / "pyproject.toml"))
        execute_python_file = make_execute_python_file()

        result = execute_python_file(file_path=str(project / "tests" / "test_x.py"))

        assert result["status"] == "error"
        assert "not a directory" in result["error"]


class TestNoProject:
    """``resolve_project_root`` returning ``None`` must not change anything."""

    def test_a_script_outside_any_project_runs_from_its_own_folder(
        self, make_execute_python_file, tmp_path, monkeypatch
    ):
        loose = tmp_path / "loose"
        loose.mkdir()
        (loose / "data.txt").write_text("payload\n", encoding="utf-8")
        (loose / "script.py").write_text(
            "import os\n"
            "print('cwd', os.getcwd())\n"
            "print('data', open('data.txt').read().strip())\n"
            "print('pythonpath', os.environ.get('PYTHONPATH', '<unset>'))\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("PYTHONPATH", raising=False)
        monkeypatch.chdir(tmp_path)
        execute_python_file = make_execute_python_file(allowed_path=tmp_path)

        result = execute_python_file(file_path=str(loose / "script.py"))

        assert result["status"] == "success", result
        assert result["return_code"] == 0, result["stderr"]
        # Relative reads resolve next to the script, and nothing is injected
        # onto the import path — the pre-fix behaviour, kept.
        assert f"cwd {loose.resolve()}" in result["stdout"]
        assert "data payload" in result["stdout"]
        assert "pythonpath <unset>" in result["stdout"]


class TestRunContext:
    def test_a_script_under_the_project_runs_from_the_project(self, project):
        run_dir, env = _python_script_run_context(
            project / "tests" / "test_x.py", project
        )

        assert run_dir == project
        assert env["PYTHONPATH"].split(os.pathsep)[0] == str(project)

    def test_an_existing_pythonpath_is_kept(self, project, monkeypatch):
        monkeypatch.setenv("PYTHONPATH", "/somewhere/else")

        _, env = _python_script_run_context(project / "tests" / "test_x.py", project)

        assert env["PYTHONPATH"] == os.pathsep.join([str(project), "/somewhere/else"])

    def test_a_script_outside_the_project_runs_from_its_own_folder(
        self, project, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("PYTHONPATH", "/untouched")
        outside = tmp_path / "elsewhere" / "script.py"
        outside.parent.mkdir()
        outside.write_text("print('hi')\n", encoding="utf-8")

        run_dir, env = _python_script_run_context(outside, project)

        assert run_dir == outside.parent.resolve()
        assert env["PYTHONPATH"] == "/untouched"

    def test_no_project_leaves_the_environment_alone(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PYTHONPATH", "/untouched")
        script = tmp_path / "loose" / "script.py"
        script.parent.mkdir()
        script.write_text("print('hi')\n", encoding="utf-8")

        run_dir, env = _python_script_run_context(script, None)

        assert run_dir == script.parent.resolve()
        assert env["PYTHONPATH"] == "/untouched"
