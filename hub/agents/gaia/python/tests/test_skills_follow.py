# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A skill added or removed in Settings reaches a chat that is already open.

The skill catalogue sits in the cached head of the system prompt, built from a
cached scan of the skill folders, so a chat open when the user adds a skill
would not list it. A real flagship runs here; only the model server is a
stand-in.
"""

from __future__ import annotations

import shutil

import pytest

from gaia import config as config_mod
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME
from gaia.llm.lemonade_manager import LemonadeManager
from tests.fixtures.fake_lemonade import FakeLemonade


@pytest.fixture
def chat(tmp_path, monkeypatch):
    """An open flagship chat, and ``add(name)`` / ``remove(name)`` for user skills."""
    from gaiapy.session_registry import build_session_agent, close_agent

    gaia_dir = tmp_path / "home"
    gaia_dir.mkdir()
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path / "work")
    monkeypatch.setenv("GAIA_HOME", str(gaia_dir))
    monkeypatch.setenv("GAIA_MEMORY_DB", str(gaia_dir / "memory.db"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon"))
    monkeypatch.setenv("GAIA_PROJECT_MAP_AUTO_INDEX", "0")
    monkeypatch.delenv("GAIA_SKILL_DISCOVERY", raising=False)
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", gaia_dir / "config.json")
    skills = gaia_dir / "skills"

    def add(name: str) -> None:
        folder = skills / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Write the {name} report for the "
            "team every Friday.\n---\n\nSteps go here.\n",
            encoding="utf-8",
        )

    def remove(name: str) -> None:
        shutil.rmtree(skills / name)

    LemonadeManager.reset()
    with FakeLemonade(
        downloaded=(DEFAULT_MODEL_NAME, "user.embeddinggemma-300m-GGUF")
    ) as fake:
        monkeypatch.setenv("LEMONADE_BASE_URL", fake.base_url)
        add("weekly-digest")
        agent = build_session_agent(allowed_paths=[str(tmp_path / "work")])
        try:
            yield agent, add, remove
        finally:
            close_agent(agent)
    LemonadeManager.reset()


def test_a_skill_added_since_the_chat_opened_is_listed(chat):
    agent, add, _ = chat
    assert "weekly-digest" in agent.system_prompt
    assert agent.follow_installed_skills() is False

    add("expense-report")  # Settings › Skills › Add
    assert agent.follow_installed_skills() is True
    assert "expense-report" in agent.system_prompt


def test_a_skill_removed_since_the_chat_opened_is_no_longer_listed(chat):
    agent, _, remove = chat
    assert "weekly-digest" in agent.system_prompt
    remove("weekly-digest")
    assert agent.follow_installed_skills() is True
    assert "weekly-digest" not in agent.system_prompt


def test_unchanged_skills_keep_the_prompt(chat):
    """Rebuilding the prompt costs the model's prompt cache; only on a change."""
    agent, _, _ = chat
    prompt = agent.system_prompt
    assert agent.follow_installed_skills() is False
    assert agent.system_prompt is prompt


def test_a_skill_added_before_a_prompt_rebuild_is_still_listed(chat):
    """A rebuild mid-turn (e.g. loading a skill) must not record a skill it didn't list."""
    agent, add, _ = chat
    assert "weekly-digest" in agent.system_prompt
    add("expense-report")
    agent.rebuild_system_prompt()
    assert "expense-report" in agent.system_prompt


def test_a_skill_folder_without_a_skill_file_is_ignored(chat, tmp_path):
    agent, _, _ = chat
    (tmp_path / "home" / "skills" / "half-copied").mkdir()
    assert agent.follow_installed_skills() is False
