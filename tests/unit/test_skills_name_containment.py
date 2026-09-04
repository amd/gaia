# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""No skill entry point may resolve a name to a directory outside the skills root.

Every one of these verbs does ``root / name`` and several then ``rmtree`` it, so
a name that is not a bare skill name is a delete outside the root: ``pathlib``
collapses ``root / "."`` to the root itself (taking ``keys/`` and the trust store
with it), leaves ``root / ".."`` at the root's parent, and lets an absolute name
replace the root outright.

The bundle-supplied name matters as much as the typed one: ``gaia skill import``
falls back to the name the *imported* ``SKILL.md`` declares, and nothing on that
path validated it.

Each test asserts the refusal **and** that the victim files are still on disk —
an exception that fired after the ``rmtree`` would pass the first half alone.
"""

from __future__ import annotations

import argparse
import os
import subprocess

import pytest

from gaia.skills.errors import SkillValidationError
from gaia.skills.install import install_skill, remove_skill
from gaia.skills.migrate import install_migrated, migrate_text
from gaia.skills.naming import skill_directory, validated_skill_name
from tests.unit.skills_helpers import isolated_manager

#: The shapes that survive ``root / name`` as a real directory outside the root.
#: ``../x`` is included because it only failed before by not existing.
ESCAPING_NAMES = [".", "..", "../x", "..\\x", "sub/nested"]


@pytest.fixture
def home(tmp_path):
    """A cold GAIA home: skills root with signing keys, trust store, and a skill."""
    gaia_home = tmp_path / "gaia-home"
    skills = gaia_home / "skills"
    (skills / "keys").mkdir(parents=True)
    (skills / "keys" / "signing.key").write_text("SECRET", encoding="utf-8")
    (skills / "trust.json").write_text("{}", encoding="utf-8")
    (skills / "web-research").mkdir()
    (skills / "web-research" / "SKILL.md").write_text(
        "---\nname: web-research\ndescription: Search the web.\n---\n\nBody.\n",
        encoding="utf-8",
    )
    gaia_home.joinpath("config.json").write_text('{"real": 1}', encoding="utf-8")
    return gaia_home


def assert_intact(home):
    """Nothing outside the named skill directory was touched."""
    skills = home / "skills"
    assert skills.is_dir()
    assert (skills / "keys" / "signing.key").read_text(encoding="utf-8") == "SECRET"
    assert (skills / "trust.json").is_file()
    assert (skills / "web-research" / "SKILL.md").is_file()
    assert (home / "config.json").is_file()


# ----------------------------------------------------------------------
# The helper itself
# ----------------------------------------------------------------------


@pytest.mark.parametrize("name", ESCAPING_NAMES + ["", "   ", "UPPER", "under_score"])
def test_validated_skill_name_refuses_anything_that_is_not_a_bare_name(name):
    with pytest.raises(SkillValidationError):
        validated_skill_name(name)


def test_skill_directory_returns_a_direct_child_for_a_good_name(tmp_path):
    assert skill_directory(tmp_path, " web-research ") == tmp_path / "web-research"


def test_skill_directory_refuses_an_absolute_name(tmp_path, home):
    with pytest.raises(SkillValidationError):
        skill_directory(tmp_path, str(home))


def test_skill_directory_refuses_a_linked_skill_directory(tmp_path, home):
    """The containment assert, not the pattern, is what catches this shape.

    ``linked`` passes NAME_PATTERN and ``is_dir()``; only resolving it shows the
    rmtree would land outside the root.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep me", encoding="utf-8")
    link = home / "skills" / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        # Windows needs admin or Developer Mode for symlinks; a junction is the
        # same shape to Path.resolve() and needs neither.
        if os.name != "nt":
            raise
        if subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
        ).returncode:
            pytest.skip("cannot create a symlink or junction on this platform")

    with pytest.raises(SkillValidationError, match="direct child"):
        skill_directory(home / "skills", "linked")
    assert (outside / "keep.txt").is_file()


# ----------------------------------------------------------------------
# gaia skill remove
# ----------------------------------------------------------------------


@pytest.mark.parametrize("name", ESCAPING_NAMES)
def test_remove_skill_refuses_an_escaping_name(home, tmp_path, name):
    manager = isolated_manager(tmp_path, user_skills_root=home / "skills")
    with pytest.raises(SkillValidationError):
        remove_skill(name, manager=manager)
    assert_intact(home)


def test_remove_skill_refuses_an_absolute_name(home, tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "important.txt").write_text("keep me", encoding="utf-8")
    manager = isolated_manager(tmp_path, user_skills_root=home / "skills")

    with pytest.raises(SkillValidationError):
        remove_skill(str(victim), manager=manager)

    assert (victim / "important.txt").is_file()
    assert_intact(home)


def test_remove_skill_still_removes_a_real_skill(home, tmp_path):
    manager = isolated_manager(tmp_path, user_skills_root=home / "skills")
    result = remove_skill("web-research", manager=manager)

    assert result.name == "web-research"
    assert not (home / "skills" / "web-research").exists()
    assert (home / "skills" / "keys" / "signing.key").is_file()


# ----------------------------------------------------------------------
# gaia skill install
# ----------------------------------------------------------------------


@pytest.mark.parametrize("name", ESCAPING_NAMES)
def test_install_skill_refuses_an_escaping_name_before_touching_the_network(
    home, tmp_path, name
):
    """The name is validated before the hub is contacted, so no fetcher is needed."""
    manager = isolated_manager(tmp_path, user_skills_root=home / "skills")

    def explode(*_args, **_kwargs):
        raise AssertionError("install must refuse the name before fetching")

    with pytest.raises(SkillValidationError):
        install_skill(name, manager=manager, fetcher=explode, force=True)
    assert_intact(home)


# ----------------------------------------------------------------------
# gaia skill import / create  (CLI handlers, called directly)
# ----------------------------------------------------------------------


def _import_args(source, *, name=None, force=True):
    return argparse.Namespace(source=str(source), name=name, force=force)


def _bundle(tmp_path, declared_name):
    """A skill directory whose SKILL.md declares ``declared_name``."""
    source = tmp_path / "bundle"
    source.mkdir()
    (source / "SKILL.md").write_text(
        f"---\nname: {declared_name}\ndescription: Imported bundle.\n---\n\nBody.\n",
        encoding="utf-8",
    )
    return source


@pytest.mark.parametrize("name", ESCAPING_NAMES)
def test_import_refuses_an_escaping_bundle_supplied_name(
    home, tmp_path, name, monkeypatch
):
    """Without --name the name comes from the bundle's own SKILL.md."""
    from gaia.skills import cli as skills_cli

    manager = isolated_manager(tmp_path, user_skills_root=home / "skills")
    monkeypatch.setattr(skills_cli, "_manager", lambda: manager)

    with pytest.raises(SkillValidationError):
        skills_cli._handle_import(_import_args(_bundle(tmp_path, name)))
    assert_intact(home)


@pytest.mark.parametrize("name", ESCAPING_NAMES)
def test_import_refuses_an_escaping_name_flag(home, tmp_path, name, monkeypatch):
    from gaia.skills import cli as skills_cli

    manager = isolated_manager(tmp_path, user_skills_root=home / "skills")
    monkeypatch.setattr(skills_cli, "_manager", lambda: manager)
    source = _bundle(tmp_path, "web-research")

    with pytest.raises(SkillValidationError):
        skills_cli._handle_import(_import_args(source, name=name))
    assert_intact(home)


def test_import_still_installs_a_well_named_bundle(home, tmp_path, monkeypatch):
    from gaia.skills import cli as skills_cli

    manager = isolated_manager(tmp_path, user_skills_root=home / "skills")
    monkeypatch.setattr(skills_cli, "_manager", lambda: manager)

    code = skills_cli._handle_import(_import_args(_bundle(tmp_path, "doc-search")))

    assert code == 0
    assert (home / "skills" / "doc-search" / "SKILL.md").is_file()


@pytest.mark.parametrize("name", ESCAPING_NAMES)
def test_create_refuses_an_escaping_name(home, tmp_path, name):
    from gaia.skills import cli as skills_cli

    args = argparse.Namespace(
        name=name,
        directory=str(home / "skills"),
        description=None,
        with_tools=False,
        force=True,
    )
    with pytest.raises(SkillValidationError):
        skills_cli._handle_create(args)
    assert_intact(home)


# ----------------------------------------------------------------------
# gaia skill migrate
# ----------------------------------------------------------------------


def _vendor_outcome(tmp_path, declared_name):
    """Migrate an OpenClaw skill whose manifest declares ``declared_name``."""
    source = tmp_path / "vendor" / "skill"
    source.mkdir(parents=True)
    source.joinpath("SKILL.md").write_text(
        f"---\nname: {declared_name}\n"
        "description: Summarize the working tree status.\n"
        "version: 0.3.0\n"
        "metadata:\n  openclaw:\n    emoji: 🌱\n"
        "---\n\n# Body\nSummarize it.\n",
        encoding="utf-8",
    )
    return migrate_text(
        source.joinpath("SKILL.md").read_text(encoding="utf-8"),
        source=source / "SKILL.md",
    )


@pytest.mark.parametrize("name", ESCAPING_NAMES)
def test_migrate_never_writes_outside_the_skills_root(home, tmp_path, name):
    """Refused, or normalized to a safe name — never a write above the root.

    ``migrate`` slugifies where it can (``sub/nested`` becomes a real skill
    name), so the guarantee under test is containment, not refusal.
    """
    root = home / "skills"
    outcome = _vendor_outcome(tmp_path, name)

    if not outcome.migrated:
        assert outcome.blockers
    else:
        try:
            target = install_migrated(outcome, root, force=True)
        except SkillValidationError:
            pass
        else:
            assert target.resolve().parent == root.resolve()

    assert_intact(home)


@pytest.mark.parametrize("name", ESCAPING_NAMES)
def test_install_migrated_refuses_a_name_that_reached_it(home, tmp_path, name):
    """The join is guarded even when the name was set after normalization."""
    outcome = _vendor_outcome(tmp_path, "git-status")
    assert outcome.migrated, outcome.blockers
    outcome.skill.name = name

    with pytest.raises(SkillValidationError):
        install_migrated(outcome, home / "skills", force=True)
    assert_intact(home)
