# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for ``gaia skill {list|info|create|import|export}`` (issue #888).

Two layers, both exercising the shipped CLI rather than a reimplementation:

- **In-process** — the real argparse tree from ``gaia.skills.cli.add_subparser``
  plus the real ``handle()`` dispatch, so every verb, flag, and exit code is
  covered cheaply.
- **Subprocess** — a handful of ``gaia skill …`` runs through ``gaia.cli`` that
  prove the top-level wiring works in the binary a user actually types
  (``test_real_cli_*``).

Every run starts cold: ``GAIA_CONFIG_DIR`` and the working directory point at
``tmp_path``, so no test reads or writes the developer's real ``~/.gaia/skills``
or ``~/.claude/skills``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from gaia.skills import cli as skills_cli
from tests.unit.skills_helpers import copy_fixture

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def skills_dir(tmp_path, monkeypatch):
    """A cold ``~/.gaia/skills`` under tmp_path, with the CWD isolated too."""
    home = tmp_path / "gaia-home"
    skills = home / "skills"
    skills.mkdir(parents=True)
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    monkeypatch.setenv("GAIA_CONFIG_DIR", str(home))
    monkeypatch.chdir(workdir)
    # A fake HOME keeps ~/.claude/skills out of discovery on the dev machine.
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "fake-home"))
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    return skills


@pytest.fixture
def run(skills_dir, capsys):
    """Parse and dispatch ``gaia skill …`` in-process; return (rc, out, err)."""

    def _run(*args: str):
        parser = argparse.ArgumentParser(prog="gaia")
        subparsers = parser.add_subparsers(dest="action")
        skills_cli.add_subparser(subparsers)
        parsed = parser.parse_args(["skill", *args])
        rc = skills_cli.handle(parsed)
        captured = capsys.readouterr()
        return rc, captured.out, captured.err

    return _run


# ----------------------------------------------------------------------
# list
# ----------------------------------------------------------------------


def test_list_from_a_cold_state(run):
    rc, out, _ = run("list")
    assert rc == 0
    assert "No skills found" in out
    assert "gaia skill create my-skill" in out


def test_list_shows_discovered_skills(run, skills_dir):
    copy_fixture("web-search", skills_dir)
    copy_fixture("bare-standard", skills_dir)

    rc, out, _ = run("list")
    assert rc == 0
    assert "web-search" in out
    assert "bare-standard" in out
    assert "verified" in out
    assert "search_web" in out


def test_list_json_is_machine_readable(run, skills_dir):
    copy_fixture("web-search", skills_dir)
    rc, out, _ = run("list", "--json")
    assert rc == 0

    payload = json.loads(out)
    assert [s["name"] for s in payload["skills"]] == ["web-search"]
    (skill,) = payload["skills"]
    assert skill["security_tier"] == "verified"
    assert skill["tools"] == ["web-search/search_web"]
    assert skill["permissions"] == ["network:read:*.brave.com"]
    assert skill["instruction_only"] is False
    assert payload["roots"][0]["label"] == "user"


def test_list_reports_a_broken_skill_and_exits_nonzero(run, skills_dir):
    copy_fixture("bare-standard", skills_dir)
    broken = skills_dir / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("not a skill\n", encoding="utf-8")

    rc, out, err = run("list")
    assert rc != 0
    assert "bare-standard" in out  # the good one still lists
    assert "failed to load" in err
    assert "broken" in err


def test_list_surfaces_shadowed_copies(run, skills_dir, tmp_path):
    """Precedence is auditable, not silent."""
    claude = tmp_path / "fake-home" / ".claude" / "skills"
    copy_fixture("bare-standard", claude)
    copy_fixture("bare-standard", skills_dir)

    rc, out, err = run("list")
    assert rc == 0
    assert out.count("bare-standard") == 1
    assert "is shadowed by the higher-precedence copy" in err


def test_list_filters_by_root(run, skills_dir):
    copy_fixture("web-search", skills_dir)
    rc, out, _ = run("list", "--root", "claude-import")
    assert rc == 0
    assert "web-search" not in out


# ----------------------------------------------------------------------
# info
# ----------------------------------------------------------------------


def test_info_shows_the_manifest(run, skills_dir):
    copy_fixture("web-search", skills_dir)
    rc, out, _ = run("info", "web-search")
    assert rc == 0
    assert "web-search  1.0.0" in out
    assert "security tier: verified" in out
    assert "network:read:*.brave.com" in out
    assert "web-search/search_web(query, max_results?)" in out


def test_info_marks_an_instruction_only_skill(run, skills_dir):
    copy_fixture("bare-standard", skills_dir)
    _, out, _ = run("info", "bare-standard")
    assert "(instruction-only)" in out


def test_info_shows_tools_required(run, skills_dir):
    copy_fixture("triage-support-ticket", skills_dir)
    _, out, _ = run("info", "triage-support-ticket")
    assert "consumes     : query_documents, read_file, remember" in out


def test_info_body_flag_prints_instructions(run, skills_dir):
    copy_fixture("bare-standard", skills_dir)
    _, out, _ = run("info", "bare-standard", "--body")
    assert "Establish the timeline" in out


def test_info_json(run, skills_dir):
    copy_fixture("web-search", skills_dir)
    _, out, _ = run("info", "web-search", "--json")
    payload = json.loads(out)
    assert payload["name"] == "web-search"
    assert payload["frontmatter"]["metadata"]["gaia"]["security_tier"] == "verified"


def test_info_on_a_missing_skill_exits_not_found(run):
    rc, _, err = run("info", "nope")
    assert rc == 3
    assert "No skill named 'nope'" in err
    assert "gaia skill create nope" in err


def test_info_on_a_claude_imported_skill_marks_it_read_only(run, tmp_path):
    claude = tmp_path / "fake-home" / ".claude" / "skills"
    copy_fixture("incident-review", claude)

    rc, out, _ = run("info", "incident-review")
    assert rc == 0
    assert "root         : claude-import (read-only)" in out
    assert "security tier: experimental" in out
    assert "(instruction-only)" in out


# ----------------------------------------------------------------------
# create
# ----------------------------------------------------------------------


def test_create_scaffolds_a_valid_skill(run, skills_dir):
    rc, out, _ = run("create", "my-skill")
    assert rc == 0
    assert "Created skill 'my-skill'" in out
    assert (skills_dir / "my-skill" / "SKILL.md").is_file()
    # The scaffold must survive the real reader.
    assert run("info", "my-skill")[0] == 0


def test_create_with_tools_scaffolds_a_loadable_pair(run, skills_dir):
    from gaia.skills import (
        parse_skill_file,
        register_skill_tools,
        unregister_skill_tools,
    )

    rc, _, _ = run(
        "create",
        "tool-skill",
        "--with-tools",
        "--description",
        "Echo text back. Use when the user asks to echo something.",
    )
    assert rc == 0
    directory = skills_dir / "tool-skill"
    assert (directory / "tools.py").is_file()
    assert "tool-skill/example_tool(text)" in run("info", "tool-skill")[1]

    # The scaffolded manifest and module must agree — otherwise the first thing
    # a new author does is hit a validation error.
    skill = parse_skill_file(directory)
    try:
        assert set(register_skill_tools(skill)) == {"tool-skill/example_tool"}
    finally:
        unregister_skill_tools("tool-skill")


def test_create_rejects_an_invalid_name(run):
    rc, _, err = run("create", "Not_Valid")
    assert rc == 4
    assert "not a valid skill name" in err


def test_create_refuses_to_clobber(run):
    run("create", "dup")
    rc, _, err = run("create", "dup")
    assert rc == 4
    assert "--force" in err


def test_create_force_overwrites(run, skills_dir):
    run("create", "dup")
    rc, _, _ = run("create", "dup", "--force", "--description", "Second take.")
    assert rc == 0
    assert "Second take." in (skills_dir / "dup" / "SKILL.md").read_text(
        encoding="utf-8"
    )


def test_create_into_an_explicit_dir(run, tmp_path):
    target = tmp_path / "bundled"
    rc, _, _ = run("create", "bundled-skill", "--dir", str(target))
    assert rc == 0
    assert (target / "bundled-skill" / "SKILL.md").is_file()


# ----------------------------------------------------------------------
# export / import
# ----------------------------------------------------------------------


def test_export_then_import_round_trips(run, skills_dir, tmp_path):
    copy_fixture("web-search", skills_dir)
    bundle = tmp_path / "web-search.zip"

    rc, out, _ = run("export", "web-search", "--output", str(bundle))
    assert rc == 0
    assert "Exported skill 'web-search'" in out
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
    assert {"web-search/SKILL.md", "web-search/tools.py"} <= names

    shutil.rmtree(skills_dir / "web-search")
    rc, out, _ = run("import", str(bundle))
    assert rc == 0
    assert "Imported skill 'web-search'" in out
    assert (skills_dir / "web-search" / "tools.py").is_file()


def test_import_resets_the_security_tier_to_experimental(run, skills_dir, tmp_path):
    source = tmp_path / "incoming"
    copy_fixture("web-search", source)

    rc, out, _ = run("import", str(source / "web-search"))
    assert rc == 0
    assert "verified → experimental" in out
    assert "security tier: experimental" in run("info", "web-search")[1]


def test_import_a_claude_code_skill(run, skills_dir, tmp_path):
    """A .claude/skills folder installs into ~/.gaia/skills unchanged."""
    claude = tmp_path / "project" / ".claude" / "skills"
    copy_fixture("incident-review", claude)

    rc, out, _ = run("import", str(claude / "incident-review"))
    assert rc == 0
    assert "Imported skill 'incident-review'" in out
    assert (skills_dir / "incident-review" / "SKILL.md").is_file()
    assert run("info", "incident-review")[0] == 0


def test_import_with_a_new_name(run, skills_dir, tmp_path):
    source = tmp_path / "incoming"
    copy_fixture("bare-standard", source)

    rc, _, _ = run("import", str(source / "bare-standard"), "--name", "renamed")
    assert rc == 0
    installed = (skills_dir / "renamed" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: renamed" in installed
    assert run("info", "renamed")[0] == 0


def test_import_refuses_to_clobber_without_force(run, tmp_path):
    source = tmp_path / "incoming"
    copy_fixture("bare-standard", source)
    run("import", str(source / "bare-standard"))

    rc, _, err = run("import", str(source / "bare-standard"))
    assert rc == 4
    assert "--force" in err

    assert run("import", str(source / "bare-standard"), "--force")[0] == 0


def test_import_rejects_a_nonexistent_source(run, tmp_path):
    rc, _, err = run("import", str(tmp_path / "ghost"))
    assert rc == 4
    assert "neither a directory" in err


def test_import_rejects_a_folder_without_a_manifest(run, tmp_path):
    empty = tmp_path / "empty-skill"
    empty.mkdir()
    rc, _, err = run("import", str(empty))
    assert rc == 4
    assert "No SKILL.md" in err


def test_import_rejects_a_traversing_zip(run, tmp_path):
    bundle = tmp_path / "evil.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("../escaped/SKILL.md", "---\nname: x\ndescription: d\n---\n")
    rc, _, err = run("import", str(bundle))
    assert rc == 4
    assert "escapes the destination" in err


def test_export_of_a_missing_skill_exits_not_found(run):
    rc, _, _ = run("export", "ghost")
    assert rc == 3


def test_export_defaults_to_the_working_directory(run, skills_dir, tmp_path):
    copy_fixture("bare-standard", skills_dir)
    rc, _, _ = run("export", "bare-standard")
    assert rc == 0
    assert (tmp_path / "workdir" / "bare-standard.zip").is_file()


# ----------------------------------------------------------------------
# Dispatch
# ----------------------------------------------------------------------


def test_missing_subcommand_returns_usage(run):
    parser = argparse.ArgumentParser(prog="gaia")
    subparsers = parser.add_subparsers(dest="action")
    skills_cli.add_subparser(subparsers)
    assert skills_cli.handle(parser.parse_args(["skill"])) == 2


def test_unknown_subcommand_returns_usage():
    args = argparse.Namespace(skill_action="teleport")
    assert skills_cli.handle(args) == 2


def test_skills_package_ships_in_the_wheel():
    """``setup.py`` lists packages explicitly — an omission ships a CLI that
    raises ImportError on the user's machine while passing every dev-mode test."""
    import ast

    tree = ast.parse((REPO_ROOT / "setup.py").read_text(encoding="utf-8"))
    declared = next(
        {ast.literal_eval(e) for e in node.value.elts}
        for node in ast.walk(tree)
        if isinstance(node, ast.keyword) and node.arg == "packages"
    )
    assert "gaia.skills" in declared

    on_disk = {
        ".".join(p.parent.relative_to(REPO_ROOT / "src").parts)
        for p in (REPO_ROOT / "src").rglob("__init__.py")
    }
    assert on_disk == declared, (
        "src/gaia packages and setup.py's explicit list have drifted; add the new "
        f"package(s) to setup.py: {sorted(on_disk - declared)}"
    )


# ----------------------------------------------------------------------
# The real binary — proves the top-level `gaia skill` wiring
# ----------------------------------------------------------------------


def _real_cli(*args: str, home: Path, cwd: Path):
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(home / "fake-home"),
        "GAIA_CONFIG_DIR": str(home / "gaia-home"),
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "GAIA_MEMORY_DISABLED": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "gaia.cli", "skill", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        check=False,
        timeout=300,
    )


@pytest.mark.slow
def test_real_cli_create_list_info_export_import(tmp_path):
    """One end-to-end pass over every verb through the real `gaia` entry point."""
    (tmp_path / "fake-home").mkdir()
    (tmp_path / "gaia-home" / "skills").mkdir(parents=True)
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    created = _real_cli("create", "smoke-skill", home=tmp_path, cwd=workdir)
    assert created.returncode == 0, created.stderr
    assert "Created skill 'smoke-skill'" in created.stdout

    listed = _real_cli("list", "--json", home=tmp_path, cwd=workdir)
    assert listed.returncode == 0, listed.stderr
    # --json must keep stdout free of log lines.
    assert [s["name"] for s in json.loads(listed.stdout)["skills"]] == ["smoke-skill"]

    info = _real_cli("info", "smoke-skill", home=tmp_path, cwd=workdir)
    assert info.returncode == 0, info.stderr
    assert "smoke-skill  0.1.0" in info.stdout

    exported = _real_cli("export", "smoke-skill", home=tmp_path, cwd=workdir)
    assert exported.returncode == 0, exported.stderr
    bundle = workdir / "smoke-skill.zip"
    assert bundle.is_file()

    shutil.rmtree(tmp_path / "gaia-home" / "skills" / "smoke-skill")
    imported = _real_cli("import", str(bundle), home=tmp_path, cwd=workdir)
    assert imported.returncode == 0, imported.stderr
    assert (tmp_path / "gaia-home" / "skills" / "smoke-skill" / "SKILL.md").is_file()


@pytest.mark.slow
def test_real_cli_help_lists_the_authoring_and_marketplace_verbs(tmp_path):
    """Every advertised verb must actually work — no discoverable dead ends.

    The marketplace verbs landed with #2467; before that this test asserted their
    absence, because a verb a user can find in --help but cannot use is worse than
    one that does not parse.
    """
    (tmp_path / "fake-home").mkdir()
    result = _real_cli("--help", home=tmp_path, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    for verb in (
        # Local authoring (#888)
        "list",
        "info",
        "create",
        "import",
        "export",
        # Marketplace (#2467)
        "search",
        "install",
        "remove",
        "lock",
        "publish",
        "keygen",
        "trust",
    ):
        assert f"    {verb}" in result.stdout, f"'{verb}' is missing from --help"


@pytest.mark.slow
def test_real_cli_missing_skill_exit_code(tmp_path):
    (tmp_path / "fake-home").mkdir()
    (tmp_path / "gaia-home" / "skills").mkdir(parents=True)
    result = _real_cli("info", "ghost", home=tmp_path, cwd=tmp_path)
    assert result.returncode == 3
    assert "No skill named 'ghost'" in result.stderr


def test_the_staleness_banner_a_user_reads_is_a_sentence(monkeypatch, capsys):
    """``age_text`` is a phrase ("93 days ago"), so the banner has to fit it."""
    from gaia.skills import cli as skills_cli
    from gaia.skills.hub import SkillSearchResult

    monkeypatch.setattr(
        "gaia.skills.hub.search_skills",
        lambda *a, **k: SkillSearchResult(
            entries=[], offline=True, age_seconds=93 * 86400, stale=True
        ),
    )

    skills_cli.handle(
        argparse.Namespace(skill_action="search", query="", hub_url=None, as_json=False)
    )

    assert "was last refreshed 93 days ago — over 7 days old" in capsys.readouterr().err
