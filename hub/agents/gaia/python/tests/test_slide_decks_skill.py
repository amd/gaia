# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The slide-decks skill on the flagship: tools register, run through the agent, stay in scope.

Calls go through ``Agent._execute_tool``, the real dispatch path, so the
skill-context binding, the automatic path checks, the LibreOffice render via
``run_program`` and the preference store are exercised as the model would hit
them. Everything is written under ``tmp_path``; GAIA_HOME is redirected so no
real preference file is touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaia.skills.manager import ROOT_AGENT_BUNDLED, SkillManager

SKILL = "slide-decks"
TOOLS = {
    f"{SKILL}/{t}"
    for t in (
        "build_deck",
        "build_deck_from_markdown",
        "verify_deck",
        "add_slide",
        "update_slide",
        "list_blueprints",
        "outline_from_blueprint",
        "deck_outline_from_markdown",
        "update_deck_prefs",
    )
}

OUTLINE = {
    "slides": [
        {
            "type": "title",
            "title": "Lakeside bakery chain review",
            "subtitle": "Owners' meeting",
        },
        {
            "type": "bullets",
            "title": "Sales grew 7% as two new shops opened",
            "bullets": ["Sales up 7%", "Two new shops"],
            "notes": "Mention the openings.",
        },
        {
            "type": "chart",
            "title": "Weekend sales drove the growth",
            "categories": ["Jan", "Feb", "Mar"],
            "series": {"Weekend": [12.1, 13.4, 14.0], "Weekday": [9.0, 9.1, 9.2]},
            "y_title": "Sales ($K)",
            "source": "till export, Jan-Mar 2026",
        },
    ]
}


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    return tmp_path_factory.mktemp("decks")


@pytest.fixture(scope="module")
def flagship(work):
    from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

    agent = GaiaAgent(
        config=GaiaAgentConfig(silent_mode=True, allowed_paths=[str(work)])
    )
    yield agent
    agent.close()


@pytest.fixture
def gaia_home(monkeypatch):
    """A short GAIA_HOME: LibreOffice's profile lives in the skill's temp folder
    beneath it and fails past the Windows path limit."""
    import shutil
    import tempfile

    home = Path(tempfile.mkdtemp(prefix="gh"))
    monkeypatch.setenv("GAIA_HOME", str(home))
    yield home
    shutil.rmtree(home, ignore_errors=True)


@pytest.fixture
def loaded(flagship, tmp_path, gaia_home):
    manager = SkillManager(
        agent_skill_dirs=flagship.SKILL_DIRS,
        user_skills_root=tmp_path / "user-skills",
        include_claude_roots=False,
    )
    before = set(flagship._tools_registry)
    skill = flagship.load_skill(SKILL, manager=manager)
    try:
        yield flagship, skill, before
    finally:
        flagship.unload_skill(SKILL)


def run(agent, tool, **args):
    return agent._execute_tool(f"{SKILL}/{tool}", args)


def test_loading_registers_the_tools_and_unloading_removes_them(
    flagship, tmp_path, monkeypatch
):
    from gaia.agents.base.tools import _TOOL_REGISTRY
    from gaia.skills.capture import code_is_deferred

    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    manager = SkillManager(
        agent_skill_dirs=flagship.SKILL_DIRS,
        user_skills_root=tmp_path / "u",
        include_claude_roots=False,
    )
    before = set(flagship._tools_registry)
    assert not TOOLS & before
    skill = flagship.load_skill(SKILL, manager=manager)
    try:
        assert skill.root == ROOT_AGENT_BUNDLED and not code_is_deferred(skill)
        assert (
            set(flagship._tools_registry) - before == TOOLS
        ), "only the skill's own tools were added"
        offered = {s["function"]["name"] for s in flagship._build_openai_tool_schemas()}
        assert TOOLS <= offered
    finally:
        assert flagship.unload_skill(SKILL)
    assert set(flagship._tools_registry) == before
    assert not TOOLS & set(_TOOL_REGISTRY)


def test_build_then_verify_through_the_agent(loaded, work):
    agent, _skill, _b = loaded
    target = work / "review.pptx"
    result = run(agent, "build_deck", outline=OUTLINE, output_path=str(target))
    assert result["status"] == "ok", result
    assert target.is_file() and result["slides"] == 3
    assert result["style_options"]
    report = run(
        agent,
        "verify_deck",
        path=str(target),
        expected_slides=3,
        render=False,
        outline=OUTLINE,
    )
    assert report["status"] == "pass", report["issues"]
    assert report["render"]["rendered"] is False


def test_render_runs_libreoffice_through_the_skill_context(loaded, work):
    from gaia.skills.context import find_program

    if find_program("soffice") is None:
        pytest.skip("LibreOffice is not installed")
    agent, _skill, _b = loaded
    target = work / "render.pptx"
    run(agent, "build_deck", outline=OUTLINE, output_path=str(target))
    pngs = work / "pngs"
    pngs.mkdir(exist_ok=True)
    report = run(
        agent, "verify_deck", path=str(target), expected_slides=3, render_dir=str(pngs)
    )
    assert report["render"]["rendered"] is True, report["render"]
    assert len(list(pngs.glob("slide_*.png"))) == 3
    assert report["status"] == "pass", report["issues"]


def test_paths_outside_the_allowed_folders_are_refused(loaded, tmp_path_factory):
    agent, _skill, _b = loaded
    elsewhere = tmp_path_factory.mktemp("not-allowed")
    target = elsewhere / "deck.pptx"
    result = run(agent, "build_deck", outline=OUTLINE, output_path=str(target))
    assert result.get("status") == "error", result
    assert not target.exists()


def test_relative_paths_and_bad_outlines_come_back_as_fixable_errors(loaded, work):
    agent, _skill, _b = loaded
    rel = run(agent, "build_deck", outline=OUTLINE, output_path="deck.pptx")
    assert rel["status"] == "error"
    bad = run(
        agent,
        "build_deck",
        outline={
            "slides": [{"type": "chart", "title": "x", "series": {"A": [1, "two"]}}]
        },
        output_path=str(work / "bad.pptx"),
    )
    assert bad["status"] == "error" and len(bad["problems"]) >= 2
    assert not (work / "bad.pptx").exists()


def test_incremental_edits_through_the_agent(loaded, work):
    agent, _skill, _b = loaded
    target = work / "grow.pptx"
    run(
        agent,
        "build_deck",
        outline={"slides": OUTLINE["slides"][:1]},
        output_path=str(target),
    )
    r = run(
        agent,
        "add_slide",
        deck_path=str(target),
        slide={"slides": OUTLINE["slides"][1:]},
    )
    assert r["status"] == "ok" and r["slides"] == 3, r
    report = run(
        agent, "verify_deck", path=str(target), expected_slides=3, render=False
    )
    assert report["status"] == "pass", report["issues"]


def test_blueprint_and_markdown_tools(loaded):
    agent, _skill, _b = loaded
    listed = run(agent, "list_blueprints")
    assert any(b["name"] == "pitch" for b in listed["blueprints"])
    plan = run(agent, "outline_from_blueprint", name="pitch", slide_count=7)
    assert len(plan["outline"]["slides"]) == 7 and len(plan["todo"]) == 7
    md = run(
        agent,
        "deck_outline_from_markdown",
        markdown="# Deck\n\n## A finding that matters\n- one\n",
    )
    assert md["status"] == "ok" and md["slides"] == 2


def test_style_feedback_is_saved_and_shapes_the_next_deck(
    loaded, work, gaia_home, monkeypatch
):
    agent, _skill, _b = loaded
    # Memory cannot start without an embedding server here, which marks the
    # session private; this test is about a normal chat, where it saves.
    monkeypatch.setattr(agent, "_incognito", False, raising=False)
    saved = run(
        agent, "update_deck_prefs", feedback="less text and a dark theme please"
    )
    assert saved["saved"] is True and saved["preferences"]["density"] == "minimal"
    stored = json.loads(
        (gaia_home / "skill-preferences" / f"{SKILL}.json").read_text(encoding="utf-8")
    )
    assert stored["theme"] == "dark"
    result = run(
        agent, "build_deck", outline=OUTLINE, output_path=str(work / "after-prefs.pptx")
    )
    assert result["theme"] == "dark"
    # An explicit request beats the saved preference.
    result = run(
        agent,
        "build_deck",
        outline=OUTLINE,
        output_path=str(work / "explicit.pptx"),
        theme="light",
    )
    assert result["theme"] == "light"
    report = run(
        agent, "verify_deck", path=str(work / "after-prefs.pptx"), render=False
    )
    assert report["limits"]["density"] == "minimal"


def test_nothing_is_saved_in_a_private_chat(loaded, monkeypatch):
    agent, _skill, _b = loaded
    monkeypatch.setattr(agent, "_incognito", True, raising=False)
    result = run(agent, "update_deck_prefs", feedback="no speaker notes")
    assert result["saved"] is False and result["note"]


def test_skill_folder_ships_its_schema_and_assets():
    from gaia_agent.agent import _bundled_skill_roots

    root = next(
        Path(r) / SKILL for r in _bundled_skill_roots() if (Path(r) / SKILL).is_dir()
    )
    assert (root / "preferences.schema.json").is_file()
    from gaia.skills.preferences import load_schema

    assert load_schema(root)["type"] == "object"
    for theme in ("light", "dark", "minimal"):
        assert (root / "templates" / f"{theme}.pptx").is_file()
