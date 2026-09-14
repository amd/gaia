# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The installed-skill catalogue the flagship shows the model (#3764).

A per-turn lexical matcher used to decide which skill a request needed. It loaded
a skill on 0 of 24 benchmark tasks, because the specifics that make a request
precise counted against every match. The model now sees every installed skill and
decides itself, so what these tests pin is that it can see them.
"""

from pathlib import Path

import pytest

from gaia.agents.base.skill_catalog import (
    CATALOG_DESCRIPTION_CHARS,
    CATALOG_ENV,
    CATALOG_HEADER,
    GROUNDING_RULE,
    catalog_env_override,
    render_catalog,
    skills_granting,
)
from gaia.skills.manager import SkillManager

_HUB_SKILLS = Path(__file__).resolve().parents[2] / "hub" / "skills"


def _write_skill(root: Path, name: str, description: str) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nBody.\n",
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def starter_pack(tmp_path_factory):
    if not (_HUB_SKILLS / "github-triage" / "SKILL.md").is_file():
        pytest.skip("starter pack not present in this checkout")
    manager = SkillManager(
        agent_skill_dirs=[_HUB_SKILLS],
        user_skills_root=tmp_path_factory.mktemp("no-user-skills"),
        include_claude_roots=False,
    )
    return manager.discover()


def test_every_installed_skill_is_listed_with_its_description(starter_pack):
    catalog = render_catalog(starter_pack)
    assert catalog.startswith(CATALOG_HEADER)
    for skill in starter_pack.values():
        assert f"- {skill.name}: {' '.join(skill.description.split())}" in catalog


def test_catalogue_is_identical_across_renders_so_it_stays_cached(starter_pack):
    first = render_catalog(starter_pack)
    reordered = dict(reversed(list(starter_pack.items())))
    assert render_catalog(reordered) == first


def test_catalogue_cost_stays_bounded(starter_pack):
    """About one line per skill. A jump here means a skill's description grew
    into documentation."""
    tokens = len(render_catalog(starter_pack)) // 4
    assert tokens < 2000, f"skill catalogue is ~{tokens} tokens"


def test_starter_descriptions_fit_under_the_cap_so_none_are_cut(starter_pack):
    longest = max(len(" ".join(s.description.split())) for s in starter_pack.values())
    assert longest <= CATALOG_DESCRIPTION_CHARS


def test_an_oversized_description_is_capped(tmp_path):
    _write_skill(tmp_path, "verbose", "word " * 204)  # 1,020 chars, format-legal
    skills = SkillManager(
        user_skills_root=tmp_path, include_claude_roots=False
    ).discover()

    (line,) = [
        ln for ln in render_catalog(skills).splitlines() if ln.startswith("- verbose:")
    ]
    assert line.endswith("…")
    assert len(line) <= len("- verbose: ") + CATALOG_DESCRIPTION_CHARS + 1


def test_header_says_not_to_reload_a_loaded_skill():
    assert "LOADED SKILLS" in CATALOG_HEADER


def test_claude_imports_are_loadable_but_not_listed(tmp_path):
    user, claude = tmp_path / "user", tmp_path / "claude"
    _write_skill(user, "note-taker", "Take notes. Use when the user dictates a note.")
    _write_skill(claude, "repo-helper", "Help Claude Code work on this repository.")
    skills = SkillManager(user_skills_root=user, claude_skill_dirs=[claude]).discover()

    assert "repo-helper" in skills
    catalog = render_catalog(skills)
    assert "- note-taker: " in catalog
    assert "repo-helper" not in catalog


def test_no_listed_skills_renders_nothing():
    assert render_catalog({}) == ""


def test_the_skill_that_grants_each_policy_cli_is_found(starter_pack):
    assert skills_granting(starter_pack, "gh") == ["github-triage"]
    assert skills_granting(starter_pack, "pytest") == ["coding"]
    assert skills_granting(starter_pack, "curl") == []


def test_grounding_rule_is_about_sourcing_and_short():
    assert "tool call in THIS turn" in GROUNDING_RULE
    assert "skill" not in GROUNDING_RULE.lower()
    assert len(GROUNDING_RULE) // 4 < 120


@pytest.mark.parametrize(
    "raw, expected", [(None, None), ("0", False), ("off", False), ("1", True)]
)
def test_env_override(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(CATALOG_ENV, raising=False)
    else:
        monkeypatch.setenv(CATALOG_ENV, raw)
    assert catalog_env_override() is expected
