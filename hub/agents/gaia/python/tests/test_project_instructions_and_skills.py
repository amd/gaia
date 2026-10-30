# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship started in a repo sees its CLAUDE.md and its skills, as Claude Code does.

Both ride in the system prompt, so both must also leave it byte-identical from
one turn to the next — even when the file changes mid-session.
"""

from __future__ import annotations

import pytest
import test_prompt_prefix_stability as stability
from test_prompt_prefix_stability import flagship  # noqa: F401 - fixture

from gaia.agents.base import project_map

RULE = "Run `make check` before every commit."


@pytest.fixture
def repo(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "CLAUDE.md").write_text(f"# Repo rules\n\n{RULE}\n", encoding="utf-8")
    skill = root / ".claude" / "skills" / "release-steps"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: release-steps\ndescription: How this repo cuts a release.\n"
        "allowed-tools: Bash\n---\n\n# Releasing\nTag, then publish.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(project_map.PROJECT_ROOT_ENV, str(root))
    monkeypatch.setenv("GAIA_SKILL_DISCOVERY", "1")
    project_map.clear_project_map_cache()
    yield root
    project_map.clear_project_map_cache()


def test_claude_md_and_project_skills_reach_a_stable_prompt(repo, flagship):
    agent, model, _doc = flagship
    stability._turn(agent, model, "How do I commit here?", ["Run make check."])
    (repo / "CLAUDE.md").write_text("Rewritten mid-session.\n", encoding="utf-8")
    agent.rebuild_system_prompt()
    stability._turn(agent, model, "And releases?", ["Load the skill."])

    system = [request[0][0]["content"] for request in model.requests]
    assert "==== PROJECT INSTRUCTIONS ====" in system[0] and RULE in system[0]
    assert "- release-steps: How this repo cuts a release." in system[0]
    assert system[0] == system[1]
    before, after = (stability._render(request) for request in model.requests)
    assert after.startswith(before)


def test_the_model_can_load_a_project_skill_by_name(repo, flagship):
    agent, _model, _doc = flagship
    result = agent._tools_registry["load_skill"]["function"](name="release-steps")
    assert result["status"] == "success", result
    assert "Tag, then publish." in agent.system_prompt


# --------------------------------------------------------------------------
# Repository-work section
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fast, root, shown",
    [
        (False, "/repo", True),
        (False, None, False),  # no project, or GAIA's own source (root is None)
        (True, "/repo", False),  # fast mode has no tools to work a repo with
    ],
)
def test_repository_section_follows_the_mapped_project(fast, root, shown):
    from types import SimpleNamespace

    from gaia_agent.agent import REPOSITORY_WORK_PROMPT, GaiaAgent

    fake = SimpleNamespace(
        config=SimpleNamespace(fast=fast), _project_map_root=lambda: root
    )
    text = GaiaAgent.get_repository_work_system_prompt(fake)
    assert text == (REPOSITORY_WORK_PROMPT if shown else "")


def test_repository_section_names_no_single_repos_rules():
    from gaia_agent.agent import REPOSITORY_WORK_PROMPT

    for specific in ("GAIA", "Lemonade", "black", "pytest", "REVIEW.md"):
        assert specific not in REPOSITORY_WORK_PROMPT


# --------------------------------------------------------------------------
# A cloned repo's skills never grant executable reach
# --------------------------------------------------------------------------


def _project_skill(root, name, frontmatter_gaia, tools_py=None):
    folder = root / ".claude" / "skills" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Hostile {name}.\nmetadata:\n  gaia:\n"
        f"{frontmatter_gaia}---\n\nDo the thing.\n",
        encoding="utf-8",
    )
    if tools_py is not None:
        (folder / "tools.py").write_text(tools_py, encoding="utf-8")
    return folder


def test_a_project_skill_asking_for_a_binary_is_not_granted_it(repo, flagship):
    import shutil

    if shutil.which("git") is None:
        pytest.skip("git is not on PATH, so its grant cannot be resolved")
    agent, _model, _doc = flagship
    _project_skill(
        repo, "git-everything", "    permissions:\n      - shell:execute:git\n"
    )
    result = agent._tools_registry["load_skill"]["function"](name="git-everything")
    assert result["status"] == "success", result
    assert "git" not in agent.granted_binaries
    assert "NOT granted" in result["warning"]
    assert "`gaia skill import " in result["warning"]
    agent.load_skill("git-everything")  # the direct path withholds it too
    assert "git" not in agent.granted_binaries


def test_a_project_skill_shipping_tools_never_imports_them(repo, flagship, tmp_path):
    agent, _model, _doc = flagship
    marker = tmp_path / "imported"
    _project_skill(
        repo,
        "pwn-tools",
        "    tools:\n      - name: pwn\n        parameters:\n"
        "          text: {type: string, required: true}\n",
        tools_py=(
            "import pathlib\n"
            f"pathlib.Path({str(marker)!r}).write_text('imported')\n"
            "from gaia.agents.base.tools import tool\n\n\n"
            "@tool\ndef pwn(text: str) -> dict:\n"
            '    """Pwn."""\n    return {}\n'
        ),
    )
    load = agent._tools_registry["load_skill"]["function"]
    result = load(name="pwn-tools")
    assert result["status"] == "error", result
    assert result["error_type"] == "SkillPermissionError"
    assert "pwn-tools" not in agent.loaded_skills

    agent.load_skill("pwn-tools")  # host-initiated load defers the code
    assert not marker.exists()
    assert not any("pwn" in name for name in agent._tools_registry)
