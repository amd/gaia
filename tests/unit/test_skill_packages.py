# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A skill's declared Python packages are checked, and installed, when it loads.

Nothing used to look at ``metadata.gaia.requirements.dependencies``: a skill
whose package was missing refused to load on the import error, or loaded and
failed mid-task. These tests pin when GAIA installs without asking, the exact
argv it runs, that an untrusted skill or a "no" never installs, that a skill
cannot smuggle an installer option in, and that a load with packages missing
says so instead of reporting tools that are not there.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("gaia_agent")

from gaia.agents import install_hints  # noqa: E402
from gaia.agents.base.agent import Agent  # noqa: E402
from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from gaia.agents.tools.skill_library_tools import (  # noqa: E402
    SkillLibraryToolsMixin,
)
from gaia.eval.tool_cost import build_doc_agent_skeleton  # noqa: E402
from gaia.installer import data_libraries  # noqa: E402
from gaia.skills import packages  # noqa: E402
from gaia.skills.errors import SkillValidationError  # noqa: E402

from .skills_helpers import isolated_manager, write_skill_dir  # noqa: E402

TOOLS = (
    "from gaia.agents.base.tools import tool\n\n\n"
    "@tool\ndef count_rows(path: str) -> dict:\n"
    '    """Count the rows of a workbook."""\n'
    '    return {"rows": 0}\n'
)


def _skill(name: str, *dependencies: str) -> str:
    listed = "".join(f"        - {d!r}\n" for d in dependencies)
    return (
        f"---\nname: {name}\ndescription: A test skill. Use when testing.\n"
        "metadata:\n  gaia:\n    requirements:\n      dependencies:\n"
        f"{listed}"
        "    tools:\n      - name: count_rows\n"
        "        description: Count the rows of a workbook\n"
        "        parameters:\n          path: {type: string, required: true}\n"
        "        returns: {type: object}\n"
        f"---\n\n# {name}\n\nBody.\n"
    )


@pytest.fixture
def env(monkeypatch):
    """A source install with pip, GAIA pinning openpyxl, and a settable installed set."""
    installed = {}
    monkeypatch.setattr(data_libraries, "_installed_version", installed.get)
    monkeypatch.setattr(
        data_libraries,
        "gaia_extra_requirements",
        lambda extras: ["pandas==2.3.3", "openpyxl==3.1.5"],
    )
    monkeypatch.setattr(install_hints, "_pip_available", lambda: True)
    monkeypatch.setattr(packages, "is_frozen", lambda: False)
    return installed


@pytest.fixture
def agent():
    return build_doc_agent_skeleton(profile="doc", deterministic=True)


def _bundled(tmp_path, *dependencies, name="sheet-tools"):
    root = tmp_path / "bundled"
    write_skill_dir(root, name, _skill(name, *dependencies), TOOLS)
    return isolated_manager(tmp_path, agent_skill_dirs=[root])


def _user(tmp_path, *dependencies, name="sheet-tools"):
    root = tmp_path / "gaia-home" / "skills"
    write_skill_dir(root, name, _skill(name, *dependencies), TOOLS)
    return isolated_manager(tmp_path)


def _installer(installs=None, returncode=0, stderr=""):
    def fake_run(argv, **kwargs):
        if installs:
            installs()
        return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)

    return patch.object(data_libraries.subprocess, "run", side_effect=fake_run)


def test_packages_already_there_install_nothing(agent, env, tmp_path):
    env.update(openpyxl="3.1.5")
    with _installer() as ran:
        agent.load_skill("sheet-tools", manager=_bundled(tmp_path, "openpyxl>=3.1"))
    assert ran.call_count == 0
    assert "sheet-tools/count_rows" in agent._tools_registry
    assert agent.skill_package_checks["sheet-tools"].installed == []


def test_a_skill_with_no_dependencies_reads_no_pins(agent, env, tmp_path, monkeypatch):
    monkeypatch.setattr(
        data_libraries,
        "gaia_extra_requirements",
        lambda extras: pytest.fail("pins read for a skill that declares nothing"),
    )
    agent.load_skill("sheet-tools", manager=_bundled(tmp_path))
    assert "sheet-tools/count_rows" in agent._tools_registry


def test_a_bundled_skill_installs_its_missing_packages_at_gaias_pin(
    agent, env, tmp_path
):
    manager = _bundled(tmp_path, "openpyxl>=3.1", "lxml>=5.0")
    with _installer(lambda: env.update(openpyxl="3.1.5", lxml="6.0.0")) as ran:
        agent.load_skill("sheet-tools", manager=manager)
    assert [call.args[0] for call in ran.call_args_list] == [
        [sys.executable, "-m", "pip", "install", "openpyxl==3.1.5", "lxml>=5.0"]
    ]
    assert "sheet-tools/count_rows" in agent._tools_registry
    assert agent.skill_package_checks["sheet-tools"].installed == [
        "openpyxl==3.1.5",
        "lxml>=5.0",
    ]


def test_only_the_skills_own_packages_are_installed(agent, env, tmp_path):
    """pandas is pinned and missing too, but this skill does not declare it."""
    with _installer(lambda: env.update(openpyxl="3.1.5")) as ran:
        agent.load_skill("sheet-tools", manager=_bundled(tmp_path, "openpyxl>=3.1"))
    assert ran.call_args.args[0][-1:] == ["openpyxl==3.1.5"]
    assert "pandas==2.3.3" not in ran.call_args.args[0]


def test_declined_libraries_load_the_skill_without_its_tools(agent, env, tmp_path):
    data_libraries.remember_choice(False)
    with _installer() as ran:
        agent.load_skill("sheet-tools", manager=_bundled(tmp_path, "openpyxl>=3.1"))
    assert ran.call_count == 0
    assert "sheet-tools" in agent.loaded_skills
    assert "sheet-tools/count_rows" not in agent._tools_registry
    check = agent.skill_package_checks["sheet-tools"]
    assert check.missing == ["openpyxl==3.1.5"]
    assert "count_rows" in check.note and "unavailable" in check.note
    assert "`gaia init --data-libraries`" in check.note


def test_full_access_installs_even_after_a_no(agent, env, tmp_path):
    data_libraries.remember_choice(False)
    agent.console.full_access = True
    with _installer(lambda: env.update(openpyxl="3.1.5")) as ran:
        agent.load_skill("sheet-tools", manager=_bundled(tmp_path, "openpyxl>=3.1"))
    assert ran.call_count == 1
    assert "sheet-tools/count_rows" in agent._tools_registry


def test_an_untrusted_skill_never_installs_even_with_full_access(agent, env, tmp_path):
    agent.console.full_access = True
    with _installer() as ran:
        agent.load_skill("sheet-tools", manager=_user(tmp_path, "lxml>=5.0"))
    assert ran.call_count == 0
    assert "sheet-tools/count_rows" not in agent._tools_registry
    note = agent.skill_package_checks["sheet-tools"].note
    assert "pip install lxml>=5.0" in note.replace('"', "")
    assert "Tell the user" in note


def test_a_failed_install_loads_without_tools_and_says_why(agent, env, tmp_path):
    with _installer(returncode=1, stderr="error: no route to pypi.org"):
        agent.load_skill("sheet-tools", manager=_bundled(tmp_path, "openpyxl>=3.1"))
    assert "sheet-tools" in agent.loaded_skills
    assert "sheet-tools/count_rows" not in agent._tools_registry
    note = agent.skill_package_checks["sheet-tools"].note
    assert "could not be installed" in note
    assert "`gaia init --data-libraries`" in note


def test_loading_again_once_the_packages_arrive_registers_the_tools(
    agent, env, tmp_path
):
    data_libraries.remember_choice(False)
    manager = _bundled(tmp_path, "openpyxl>=3.1")
    agent.load_skill("sheet-tools", manager=manager)
    assert "sheet-tools/count_rows" not in agent._tools_registry

    env.update(openpyxl="3.1.5")  # the user ran the command
    agent.load_skill("sheet-tools", manager=manager)

    assert "sheet-tools/count_rows" in agent._tools_registry
    assert agent.skill_package_checks["sheet-tools"].ok


def test_unloading_forgets_the_check(agent, env, tmp_path):
    data_libraries.remember_choice(False)
    agent.load_skill("sheet-tools", manager=_bundled(tmp_path, "openpyxl>=3.1"))
    agent.unload_skill("sheet-tools")
    assert "sheet-tools" not in agent.skill_package_checks


@pytest.mark.parametrize(
    "spec",
    [
        "https://evil.example/pkg.whl",
        "pkg @ https://evil.example/pkg.whl",
        "git+https://github.com/x/y",
        "--index-url https://evil.example/simple",
        "pkg --extra-index-url https://evil.example/simple",
        "../local",
        "pkg[extra]>=1",
    ],
)
def test_a_requirement_that_is_not_a_plain_package_refuses_the_load(
    agent, env, tmp_path, spec
):
    with _installer() as ran:
        with pytest.raises(SkillValidationError, match="not a plain package"):
            agent.load_skill("sheet-tools", manager=_bundled(tmp_path, spec))
    assert ran.call_count == 0
    assert "sheet-tools" not in agent.loaded_skills


def test_a_frozen_build_checks_nothing(agent, env, tmp_path, monkeypatch):
    """The binary has no package metadata, so a check would hide every tool."""
    monkeypatch.setattr(packages, "is_frozen", lambda: True)
    with _installer() as ran:
        agent.load_skill("sheet-tools", manager=_bundled(tmp_path, "openpyxl>=3.1"))
    assert ran.call_count == 0
    assert "sheet-tools/count_rows" in agent._tools_registry


def test_stale_install_metadata_falls_back_to_the_skills_own_range(
    agent, env, tmp_path, monkeypatch
):
    def no_extra(extras):
        raise RuntimeError("amd-gaia declares no extra ['data-libraries'].")

    monkeypatch.setattr(data_libraries, "gaia_extra_requirements", no_extra)
    with _installer(lambda: env.update(openpyxl="3.1.5")) as ran:
        agent.load_skill("sheet-tools", manager=_bundled(tmp_path, "openpyxl>=3.1"))
    assert ran.call_args.args[0][-1] == "openpyxl>=3.1"


class _LibraryAgent(Agent, SkillLibraryToolsMixin):
    def _get_system_prompt(self):
        return "Use tools."

    def _register_tools(self):
        self.register_skill_library_tools()


class TestTheLoadResult:
    """What the ``load_skill`` tool hands the model."""

    def _activate(self, manager):
        saved = dict(_TOOL_REGISTRY)
        _TOOL_REGISTRY.clear()
        try:
            with patch("gaia.agents.base.agent.AgentSDK"):
                agent = _LibraryAgent(silent_mode=True, skip_lemonade=True)
            agent._skill_manager = manager
            return agent.activate_skill("sheet-tools")
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved)

    def test_missing_packages_are_named_and_no_tool_is_claimed(self, env, tmp_path):
        data_libraries.remember_choice(False)
        payload = self._activate(_bundled(tmp_path, "openpyxl>=3.1"))
        assert payload["status"] == "success"
        assert payload["registered_tools"] == []
        assert payload["missing_packages"] == ["openpyxl==3.1.5"]
        assert payload["unavailable_tools"] == ["count_rows"]
        assert "`gaia init --data-libraries`" in payload["warning"]

    def test_an_install_is_reported(self, env, tmp_path):
        manager = _bundled(tmp_path, "openpyxl>=3.1")
        with _installer(lambda: env.update(openpyxl="3.1.5")):
            payload = self._activate(manager)
        assert payload["installed_packages"] == ["openpyxl==3.1.5"]
        assert payload["registered_tools"] == ["sheet-tools/count_rows"]
        assert "warning" not in payload
