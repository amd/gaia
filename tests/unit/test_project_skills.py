# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A project's ``.claude/skills`` are listed and loadable, as in Claude Code.

The repository the agent is started in says how to work on it through these
skills. They were discovered but hidden from the catalogue, so the model never
knew they existed; and a real-world Claude Code skill GAIA's stricter schema
rejected was dropped with only a log line.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from gaia.agents.base.skill_catalog import CATALOG_DESCRIPTION_CHARS, render_catalog
from gaia.skills import (
    CLAUDE_ROOTS,
    ROOT_CLAUDE_IMPORT,
    ROOT_CLAUDE_PROJECT,
    SkillManager,
    SkillValidationError,
)
from gaia.skills.format import parse_project_skill_file

REPO_ROOT = Path(__file__).resolve().parents[2]


def _skill(root: Path, folder: str, frontmatter: str, body: str = "Body.\n") -> Path:
    path = root / ".claude" / "skills" / folder
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\n{frontmatter}---\n\n{body}", encoding="utf-8")
    return path


def _manager(project: Path, home: Path, **kwargs) -> SkillManager:
    return SkillManager(
        user_skills_root=home / "gaia-skills", project_dir=project, **kwargs
    )


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir()
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return project, home


# ── roots and the catalogue ──────────────────────────────────────────────


def test_project_skills_are_listed_and_user_claude_skills_are_not(dirs):
    project, home = dirs
    _skill(project, "repo-rules", "name: repo-rules\ndescription: How to work here.\n")
    _skill(home, "personal", "name: personal\ndescription: My own habits.\n")
    skills = _manager(project, home).discover()
    assert skills["repo-rules"].root == ROOT_CLAUDE_PROJECT
    assert skills["personal"].root == ROOT_CLAUDE_IMPORT
    catalog = render_catalog(skills)
    assert "- repo-rules: How to work here." in catalog
    assert "personal" not in catalog


def test_without_a_project_dir_cwd_skills_stay_unlisted(dirs, monkeypatch):
    """No project known (e.g. GAIA's own checkout): the old, unlisted import."""
    project, home = dirs
    _skill(project, "repo-rules", "name: repo-rules\ndescription: How to work here.\n")
    monkeypatch.chdir(project)
    skills = SkillManager(user_skills_root=home / "gaia-skills").discover()
    assert skills["repo-rules"].root == ROOT_CLAUDE_IMPORT
    assert render_catalog(skills) == ""


def test_an_explicit_claude_dir_override_keeps_the_import_label(dirs):
    project, home = dirs
    _skill(project, "repo-rules", "name: repo-rules\ndescription: How to work here.\n")
    skills = _manager(
        project, home, claude_skill_dirs=[project / ".claude" / "skills"]
    ).discover()
    assert skills["repo-rules"].root == ROOT_CLAUDE_IMPORT


def test_a_project_skill_loads_by_name_with_its_body(dirs):
    project, home = dirs
    _skill(
        project,
        "repo-rules",
        "name: repo-rules\ndescription: How to work here.\n",
        "# Rules\nAlways run the linter.\n",
    )
    skill = _manager(project, home).load("repo-rules")
    assert skill.body == "# Rules\nAlways run the linter."
    assert skill.read_only and skill.root in CLAUDE_ROOTS


def test_a_user_gaia_skill_still_shadows_a_project_skill(dirs):
    project, home = dirs
    _skill(project, "dup", "name: dup\ndescription: From the repo.\n")
    user = home / "gaia-skills" / "dup"
    user.mkdir(parents=True)
    (user / "SKILL.md").write_text(
        "---\nname: dup\ndescription: From the user.\n---\nB\n", encoding="utf-8"
    )
    manager = _manager(project, home)
    assert manager.get("dup").description == "From the user."
    assert [s.root for s in manager.shadowed("dup")] == [ROOT_CLAUDE_PROJECT]


# ── Claude Code frontmatter GAIA's schema rejects ─────────────────────────


@pytest.mark.parametrize(
    "folder, frontmatter, expected",
    [
        ("Deploy_Helper", "name: Deploy_Helper\ndescription: d\n", "deploy-helper"),
        ("anything", "name: Other Name\ndescription: d\n", "other-name"),
        ("from-dir", "description: d\n", "from-dir"),
        ("vers", "name: vers\ndescription: d\nversion: 1.0\n", "vers"),
        (
            "extras",
            "name: extras\ndescription: d\nallowed-tools: Bash, Read\n"
            "argument-hint: '[file]'\nmodel: opus\nmetadata: not-a-map\n",
            "extras",
        ),
    ],
)
def test_claude_code_skills_load_despite_gaia_schema(
    dirs, caplog, folder, frontmatter, expected
):
    project, home = dirs
    _skill(project, folder, frontmatter)
    with caplog.at_level(logging.WARNING):
        manager = _manager(project, home)
        skills = manager.discover()
    assert expected in skills, manager.discovery_errors
    assert manager.discovery_errors == {}
    assert manager.load(expected).body == "Body."
    assert expected in render_catalog(skills)


def test_a_long_description_is_kept_and_trimmed_only_in_the_catalogue(dirs):
    project, home = dirs
    _skill(project, "wordy", f"name: wordy\ndescription: {'w' * 1500}\n")
    skills = _manager(project, home).discover()
    assert len(skills["wordy"].description) == 1500
    rendered = render_catalog(skills)
    assert "w" * CATALOG_DESCRIPTION_CHARS + "…" in rendered
    assert "w" * (CATALOG_DESCRIPTION_CHARS + 1) not in rendered


def test_a_missing_description_is_the_first_paragraph(dirs):
    project, home = dirs
    _skill(project, "nodesc", "name: nodesc\n", "# Title\n\nDoes the thing.\nWell.\n")
    assert _manager(project, home).get("nodesc").description == "Does the thing. Well."


def test_a_valid_gaia_skill_parses_unchanged(dirs, caplog):
    project, home = dirs
    path = _skill(project, "clean", "name: clean\ndescription: Fine.\n")
    with caplog.at_level(logging.WARNING):
        skill = parse_project_skill_file(path, root=ROOT_CLAUDE_PROJECT)
    assert skill.name == "clean" and "not a valid GAIA skill" not in caplog.text


@pytest.mark.parametrize(
    "folder, text",
    [
        ("no-frontmatter", "Just markdown.\n"),
        ("bad-yaml", "---\nname: [unclosed\n---\nB\n"),
        ("empty", "---\nname: empty\n---\n\n"),
        ("symbols", "---\nname: '!!!'\ndescription: d\n---\nB\n"),
        ("toolong", f"---\nname: {'a' * 80}\ndescription: d\n---\nB\n"),
    ],
)
def test_a_skill_nothing_can_be_made_of_is_reported_not_dropped(dirs, folder, text):
    project, home = dirs
    path = project / ".claude" / "skills" / folder
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(text, encoding="utf-8")
    manager = _manager(project, home)
    assert manager.discover() == {}
    assert str(path) in manager.discovery_errors
    with pytest.raises(SkillValidationError):
        parse_project_skill_file(path, root=ROOT_CLAUDE_PROJECT)


def test_malformed_gaia_metadata_grants_nothing(dirs, caplog):
    project, home = dirs
    _skill(
        project,
        "grabby",
        "name: grabby\ndescription: d\nmetadata:\n  gaia:\n"
        "    permissions: ['not a permission!!']\n",
    )
    with caplog.at_level(logging.WARNING):
        skill = _manager(project, home).load("grabby")
    assert skill.gaia.permissions == [] and "ignoring its metadata.gaia" in caplog.text


def test_lenient_parsing_is_only_for_the_project_root(dirs):
    """~/.claude/skills keeps GAIA's schema: an invalid one is still an error."""
    project, home = dirs
    _skill(home, "Bad_Name", "name: Bad_Name\ndescription: d\n")
    manager = _manager(project, home)
    assert manager.discover() == {}
    assert len(manager.discovery_errors) == 1


# ── this repository's own skills ─────────────────────────────────────────


def test_this_repos_skills_all_load_as_project_skills(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    manager = SkillManager(user_skills_root=tmp_path / "s", project_dir=REPO_ROOT)
    skills = manager.discover()
    on_disk = {
        p.parent.name for p in (REPO_ROOT / ".claude" / "skills").glob("*/SKILL.md")
    }
    assert on_disk and set(skills) == on_disk, manager.discovery_errors
    skill = manager.load("github-issue-response")
    assert skill.root == ROOT_CLAUDE_PROJECT
    assert "Responding on GitHub" in skill.body
    assert "- github-issue-response: " in render_catalog(skills)


def test_a_repo_skill_never_gets_executable_reach_on_load():
    """A cloned repo's skill asking for a shell binary is deferred, not granted."""
    from types import SimpleNamespace

    from gaia.skills import capture
    from gaia.skills.manager import ROOT_CLAUDE_PROJECT

    skill = SimpleNamespace(
        gaia=SimpleNamespace(tools=["x"]), root=ROOT_CLAUDE_PROJECT, name="evil"
    )
    assert capture.code_is_deferred(skill) is True


@pytest.mark.parametrize(
    "root, tools, expected",
    [
        (
            ROOT_CLAUDE_PROJECT,
            [],
            "`gaia skill import {d}`, then `gaia skill promote evil`",
        ),
        (
            ROOT_CLAUDE_PROJECT,
            ["x"],
            "`gaia skill import {d} --allow-experimental`, then "
            "`gaia skill promote evil`",
        ),
        ("user", ["x"], "`gaia skill promote evil`"),
    ],
)
def test_the_deferral_names_a_command_that_works_for_its_root(
    tmp_path, root, tools, expected
):
    """``gaia skill promote`` only sees the user root: for a repo's copy it fails."""
    from types import SimpleNamespace

    from gaia.skills.capture import deferred_remedy

    directory = tmp_path / ".claude" / "skills" / "evil"
    skill = SimpleNamespace(
        name="evil", root=root, gaia=SimpleNamespace(tools=tools), directory=directory
    )
    assert deferred_remedy(skill) == expected.format(d=directory)
