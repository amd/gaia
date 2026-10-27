# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The installed-skill catalogue the flagship shows the model (#3764).

A per-turn lexical matcher used to decide which skill a request needed. It loaded
a skill on 0 of 24 benchmark tasks, because the specifics that make a request
precise counted against every match. The model now sees every installed skill and
decides itself, so what these tests pin is that it can see them.
"""

import logging
from pathlib import Path

import pytest

from gaia.agents.base.skill_catalog import (
    CATALOG_DESCRIPTION_CHARS,
    CATALOG_ENV,
    CATALOG_HEADER,
    CATALOG_PAIRS_SHOWN,
    GROUNDING_RULE,
    catalog_env_override,
    companions,
    render_catalog,
    skills_granting,
)
from gaia.skills.manager import SkillManager

_HUB_SKILLS = Path(__file__).resolve().parents[2] / "hub" / "skills"


def _write_skill(
    root: Path, name: str, description: str, pairs_with: tuple[str, ...] = ()
) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    pairs = ""
    if pairs_with:
        pairs = "metadata:\n  gaia:\n    pairs_with:\n" + "".join(
            f"      - {p}\n" for p in pairs_with
        )
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{pairs}---\n\nBody.\n",
        encoding="utf-8",
    )


def _discover(root: Path, **kwargs):
    return SkillManager(
        user_skills_root=root, include_claude_roots=False, **kwargs
    ).discover()


def _line(catalog: str, name: str) -> str:
    (line,) = [ln for ln in catalog.splitlines() if ln.startswith(f"- {name}:")]
    return line


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


def test_starter_pairs_name_only_skills_or_known_pending_ones(starter_pack):
    """A typo in pairs_with is silently inert at runtime, so catch it here."""
    pending = {"office-docs"}  # ships separately; the pair lights up once it lands
    for skill in starter_pack.values():
        unknown = set(skill.gaia.pairs_with) - set(starter_pack) - pending
        assert not unknown, f"{skill.name} pairs with unknown skill(s) {unknown}"


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
    catalog = render_catalog(starter_pack)
    for skill in starter_pack.values():
        whole = f"- {skill.name}: {' '.join(skill.description.split())}"
        line = _line(catalog, skill.name)
        assert line == whole or line.startswith(
            whole + " (pairs with: "
        ), f"{skill.name} was cut"


def test_an_oversized_description_is_capped(tmp_path):
    _write_skill(tmp_path, "verbose", "word " * 204)  # 1,020 chars, format-legal
    line = _line(render_catalog(_discover(tmp_path)), "verbose")
    assert line.endswith("…")
    assert len(line) <= len("- verbose: ") + CATALOG_DESCRIPTION_CHARS + 1


def test_header_says_not_to_reload_a_loaded_skill():
    assert "LOADED SKILLS" in CATALOG_HEADER


def test_header_says_to_load_every_matching_skill_not_just_one():
    """Told "when the work matches a skill", models loaded one skill for a deck
    built from documents, and none when three fit a workbook request."""
    header = " ".join(CATALOG_HEADER.split())
    assert "EVERY skill whose description matches any part of the request" in header
    assert "load each one before starting" in header
    assert "partway" in header
    assert len(CATALOG_HEADER) // 4 < 150


def test_pairs_render_on_both_sides_of_the_pair(tmp_path):
    _write_skill(tmp_path, "deck", "Build a deck.", pairs_with=("sources", "data"))
    _write_skill(tmp_path, "sources", "Read the user's documents.")
    _write_skill(tmp_path, "data", "Query a spreadsheet.")
    _write_skill(tmp_path, "loner", "Do something else.")
    catalog = render_catalog(_discover(tmp_path))

    assert _line(catalog, "deck") == "- deck: Build a deck. (pairs with: sources, data)"
    assert _line(catalog, "sources").endswith("(pairs with: deck)")
    assert _line(catalog, "data").endswith("(pairs with: deck)")
    assert "pairs with" not in _line(catalog, "loner")


def test_a_companion_that_is_not_installed_is_ignored(tmp_path, caplog):
    _write_skill(tmp_path, "deck", "Build a deck.", pairs_with=("absent", "deck"))
    skills = _discover(tmp_path)

    with caplog.at_level(logging.DEBUG, logger="gaia.agents.base.skill_catalog"):
        assert companions(skills, "deck") == []
    assert "'absent', which is not installed" in caplog.text
    assert _line(render_catalog(skills), "deck") == "- deck: Build a deck."


def test_an_unlisted_import_is_not_named_as_a_companion(tmp_path):
    user, claude = tmp_path / "user", tmp_path / "claude"
    _write_skill(user, "deck", "Build a deck.", pairs_with=("repo-helper",))
    _write_skill(claude, "repo-helper", "Help on this repo.", pairs_with=("deck",))
    skills = SkillManager(user_skills_root=user, claude_skill_dirs=[claude]).discover()

    assert companions(skills, "deck") == []


def test_a_popular_skill_keeps_its_whole_description(tmp_path):
    """Pairs grow with the hub; they must never eat the description the model
    decides from. Many skills pairing with one hub skill is the real shape."""
    description = "Build Office files. " + "word " * 80
    _write_skill(tmp_path, "office", description)
    names = [f"companion-{i:02d}" for i in range(12)]
    for name in names:
        _write_skill(tmp_path, name, "Read some source.", pairs_with=("office",))
    skills = _discover(tmp_path)
    line = _line(render_catalog(skills), "office")

    assert f"- office: {' '.join(description.split())} (pairs with: " in line
    assert line.endswith(f"{', '.join(names[:CATALOG_PAIRS_SHOWN])}, +9 more)")
    assert companions(skills, "office") == names  # load_skill still names all


def test_a_line_stays_bounded_however_many_pairs(tmp_path):
    long_names = [("x" * 60) + f"-{i:02d}" for i in range(20)]
    _write_skill(tmp_path, "verbose", "word " * 204, pairs_with=tuple(long_names))
    for name in long_names:
        _write_skill(tmp_path, name, "Other.")
    line = _line(render_catalog(_discover(tmp_path)), "verbose")

    assert "…" in line and line.endswith(", +17 more)")
    assert len(line) <= len("- verbose: ") + CATALOG_DESCRIPTION_CHARS + 1 + 240


def test_the_skills_own_pairs_are_shown_first(tmp_path):
    _write_skill(tmp_path, "deck", "Build a deck.", pairs_with=("zeta",))
    for name in ("alpha", "beta", "gamma"):
        _write_skill(tmp_path, name, "Source.", pairs_with=("deck",))
    _write_skill(tmp_path, "zeta", "Source.")

    assert companions(_discover(tmp_path), "deck") == ["zeta", "alpha", "beta", "gamma"]


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
