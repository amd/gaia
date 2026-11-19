# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A skill is in use when its own tools run, whether or not the model loaded it.

Local Qwen ran analyze-recording's whole pipeline by calling its tools directly,
so the skill's instructions never reached it and the chat never said a skill
was in use. ``metadata.gaia.loads_with`` names the tools only that skill drives;
calling one loads the skill and tells the console, once per turn.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent")

from gaia.eval.tool_cost import build_doc_agent_skeleton  # noqa: E402

from .skills_helpers import isolated_manager, write_skill_dir  # noqa: E402

SKILL = (
    "---\nname: recorder\ndescription: Breaks down recordings. Use when testing.\n"
    "metadata:\n  gaia:\n    tools_required:\n      - read_file\n      - fake_analyze\n"
    "    loads_with:\n      - fake_analyze\n"
    "---\n\n# recorder\n\nCall fake_analyze on the transcript, then read_file.\n"
)


class _Console:
    def __init__(self, inner):
        self._inner = inner
        self.uses = []

    def print_skill_use(self, name, loaded, how):
        self.uses.append((name, list(loaded), how))

    def __getattr__(self, attr):
        return getattr(self._inner, attr)


@pytest.fixture
def agent(tmp_path):
    root = tmp_path / "skills"
    write_skill_dir(root, "recorder", SKILL)
    agent = build_doc_agent_skeleton(profile="doc", deterministic=True)
    agent._skill_manager = isolated_manager(tmp_path, agent_skill_dirs=[root])
    agent._tools_registry["fake_analyze"] = {
        "name": "fake_analyze",
        "description": "Analyze.",
        "parameters": {},
        "function": lambda: {"status": "success"},
    }
    agent.console = _Console(agent.console)
    agent._turn_seq = 1
    return agent


def test_calling_a_loads_with_tool_loads_its_skill(agent):
    assert "recorder" not in agent.loaded_skills

    result = agent._execute_tool("fake_analyze", {})

    assert result == {"status": "success"}
    assert "recorder" in agent.loaded_skills
    assert agent.console.uses == [("recorder", list(agent.loaded_skills), "auto")]
    assert {"name": "recorder", "how": "auto"} in agent.describe_skills()


def test_a_tool_the_skill_only_shares_loads_nothing(agent):
    agent._note_skill_tool_use("read_file")

    assert "recorder" not in agent.loaded_skills
    assert agent.console.uses == []


def test_an_already_loaded_skill_is_announced_once_per_turn(agent):
    agent.load_skill("recorder")

    agent._note_skill_tool_use("fake_analyze")
    agent._note_skill_tool_use("fake_analyze")
    agent._turn_seq = 2
    agent._note_skill_tool_use("fake_analyze")

    assert [how for _, _, how in agent.console.uses] == ["loaded", "loaded"]
    # Loaded by the agent itself, before any announcement: it reads as always on.
    assert {"name": "recorder", "how": "always"} in agent.describe_skills()


def test_a_skill_the_model_loaded_keeps_that_origin(agent):
    agent.load_skill("recorder")
    agent._announce_skill("recorder", "model")
    agent._turn_seq = 2

    agent._note_skill_tool_use("fake_analyze")

    assert {"name": "recorder", "how": "model"} in agent.describe_skills()
    assert [how for _, _, how in agent.console.uses] == ["model", "loaded"]


CODE_SKILL = SKILL.replace("name: recorder", "name: coder").replace(
    "    loads_with:",
    "    tools:\n      - name: helper\n        description: Helps.\n    loads_with:",
)


@pytest.mark.parametrize(
    "where",
    ["user_skills_root", "claude_skill_dirs"],
)
def test_a_skill_nobody_shipped_with_the_agent_never_loads_itself(tmp_path, where):
    root = tmp_path / "elsewhere"
    write_skill_dir(root, "recorder", SKILL)
    agent = build_doc_agent_skeleton(profile="doc", deterministic=True)
    agent._skill_manager = isolated_manager(
        tmp_path, **{where: root if where == "user_skills_root" else [root]}
    )
    assert "recorder" in agent.skill_manager.discover()

    assert agent._skill_loaded_by("fake_analyze") is None


def test_a_bundled_skill_that_ships_code_never_loads_itself(tmp_path):
    root = tmp_path / "bundled"
    write_skill_dir(root, "coder", CODE_SKILL)
    agent = build_doc_agent_skeleton(profile="doc", deterministic=True)
    agent._skill_manager = isolated_manager(tmp_path, agent_skill_dirs=[root])
    assert agent.skill_manager.discover()["coder"].gaia.tools

    assert agent._skill_loaded_by("fake_analyze") is None


def test_an_automatic_load_registers_the_tools_the_framework_supplies(agent):
    activated = []
    agent._activate_extraction_skills = activated.append

    agent._note_skill_tool_use("fake_analyze")

    assert activated == ["recorder"]


def test_a_skill_that_cannot_load_does_not_stop_its_tool(agent, monkeypatch, caplog):
    from gaia.skills.errors import SkillUnavailableError

    def refuse(name, **_):
        raise SkillUnavailableError(f"Skill '{name}' needs a larger model.")

    monkeypatch.setattr(agent, "load_skill", refuse)

    assert agent._execute_tool("fake_analyze", {}) == {"status": "success"}
    assert agent.console.uses == []
    assert "could not be loaded" in caplog.text
