# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The skill's manifest, tools.py and audit agree; the tools run inside a bound call.

This is the core-lane twin of the flagship test in
``hub/agents/gaia/python/tests/test_slide_decks_skill.py``: no agent package
needed. Calls are bound with :func:`gaia.skills.context.bind` to a stand-in
agent with no path validator (library use, no sandbox declared), and
GAIA_HOME is redirected so preference writes land under tmp_path.
"""

from __future__ import annotations

import pytest

from gaia.skills import parse_skill_file, validate_skill
from gaia.skills.audit import audit_skill
from gaia.skills.context import bind
from gaia.skills.loader import register_skill_tools, unregister_skill_tools

from .conftest import SKILL_DIR

TOOL_NAMES = {
    "build_deck",
    "build_deck_from_markdown",
    "verify_deck",
    "add_slide",
    "update_slide",
    "list_blueprints",
    "outline_from_blueprint",
    "deck_outline_from_markdown",
    "update_deck_prefs",
}


class _Host:
    """Enough of an agent for gaia.skills.context: not private, no validator."""

    _incognito = False


@pytest.fixture
def tools(monkeypatch, tmp_path):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    skill = parse_skill_file(SKILL_DIR)
    before = dict(_TOOL_REGISTRY)
    registered = register_skill_tools(skill)
    try:
        yield skill, {
            k.split("/", 1)[1]: v["function"] for k, v in registered.items()
        }, registered
    finally:
        unregister_skill_tools(skill.name)
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(before)


def test_manifest_validates_and_declares_every_tool(tools):
    skill, fns, registered = tools
    validate_skill(skill, source=str(SKILL_DIR))
    assert set(fns) == TOOL_NAMES
    assert {t.name for t in skill.gaia.tools} == TOOL_NAMES
    assert (
        registered["slide-decks/build_deck"]["parameters"]["outline"]["type"]
        == "object"
    )
    assert (
        registered["slide-decks/build_deck"]["parameters"]["output_path"]["required"]
        is True
    )
    perms = {str(p) for p in skill.parsed_permissions()}
    assert {"program:run:soffice", "scratch:write"} <= perms


def test_the_skill_code_passes_the_bundled_audit():
    report = audit_skill(SKILL_DIR)
    assert report.verdict == "ALLOW", [
        (f.severity, f.location, f.message) for f in report.findings
    ]
    assert not [
        f for f in report.findings if f.severity in ("medium", "high", "critical")
    ]


def test_tools_build_verify_edit_and_save_prefs(tools, tmp_path):
    skill, fns, _r = tools
    target = tmp_path / "deck.pptx"
    outline = {
        "slides": [
            {"type": "title", "title": "Pine Ridge school district update"},
            {
                "type": "bullets",
                "title": "Enrolment rose 3% for a second year",
                "bullets": ["Up 3%", "Two schools full"],
            },
        ]
    }
    with bind(_Host(), skill):
        built = fns["build_deck"](outline=outline, output_path=str(target))
        assert built["status"] == "ok", built
        report = fns["verify_deck"](path=str(target), expected_slides=2, render=False)
        assert report["status"] == "pass", report["issues"]
        added = fns["add_slide"](
            deck_path=str(target), slide={"type": "section", "title": "Budget"}
        )
        assert added["slides"] == 3
        replaced = fns["update_slide"](
            deck_path=str(target),
            index=3,
            slide={"type": "section", "title": "Budget and staffing"},
        )
        assert replaced["slides"] == 3
        saved = fns["update_deck_prefs"](feedback="use our green #2E7D32")
        assert saved["saved"] and saved["preferences"]["colors"]["primary"] == "2E7D32"
        again = fns["build_deck"](
            outline=outline, output_path=str(tmp_path / "branded.pptx")
        )
        assert again["status"] == "ok"
    assert (tmp_path / "home" / "skill-preferences" / "slide-decks.json").is_file()


def test_tool_errors_are_returned_not_raised(tools, tmp_path):
    skill, fns, _r = tools
    with bind(_Host(), skill):
        assert (
            fns["build_deck"](
                outline={"slides": []}, output_path=str(tmp_path / "x.pptx")
            )["status"]
            == "error"
        )
        assert (
            fns["build_deck"](
                outline={"slides": [{"type": "title", "title": "x"}]},
                output_path="relative.pptx",
            )["status"]
            == "error"
        )
        assert (
            fns["build_deck"](
                outline={"slides": [{"type": "title", "title": "x"}]},
                output_path=str(tmp_path / "x.docx"),
            )["status"]
            == "error"
        )
        assert (
            fns["build_deck"](
                outline={"slides": [{"type": "title", "title": "x"}]},
                output_path=str(tmp_path / "no-such-dir" / "x.pptx"),
            )["status"]
            == "error"
        )
        assert (
            fns["build_deck"](
                outline={"slides": [{"type": "title", "title": "x"}]},
                output_path=str(SKILL_DIR / "x.pptx"),
            )["status"]
            == "error"
        )
        assert (
            fns["verify_deck"](path=str(tmp_path / "missing.pptx"))["status"] == "error"
        )
        assert (
            fns["outline_from_blueprint"](name="no_such_blueprint")["status"] == "error"
        )
        assert fns["deck_outline_from_markdown"](markdown="")["status"] == "error"
        bad = fns["update_deck_prefs"](changes={"density": "huge"})
        assert bad["status"] == "error"
    assert not (SKILL_DIR / "x.pptx").exists()


def test_the_procedure_asks_at_most_once_and_never_blocks():
    """The rule for asking: one question, with a default and a short timeout."""
    import re

    body = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "request_user_input" in body
    assert re.search(r"Ask \*\*one\*\* short question \*\*only\*\*", body)
    assert "Never ask about anything a default covers" in body
    assert "references/brief-and-sources.md" in body
    ref = (SKILL_DIR / "references" / "brief-and-sources.md").read_text(
        encoding="utf-8"
    )
    timeouts = [int(t) for t in re.findall(r"timeout_seconds=(\d+)", ref)]
    assert timeouts and all(t <= 60 for t in timeouts)
    assert "default_if_no_response=" in ref
    # The example of a request that must NOT trigger a question is there too.
    assert "no question" in ref


def test_markdown_tool_and_required_items_through_the_tool_layer(tools, tmp_path):
    skill, fns, _r = tools
    md = (
        "# Harbour ferry update\n\n"
        "## Crossings rose to 1,180 a week after the timetable change\n"
        "- 1,180 weekly crossings, up from 960\n"
        "Notes: the pier repair finishes in May.\n"
    )
    with bind(_Host(), skill):
        r = fns["build_deck_from_markdown"](
            markdown=md,
            output_path=str(tmp_path / "ferry.pptx"),
            required_items=["1,180 weekly crossings", "pier repair finishes in May"],
        )
        assert r["status"] == "built_with_issues"
        assert r["verify"]["summary"]["required_in_notes_only"] == 1
        report = fns["verify_deck"](
            path=str(tmp_path / "ferry.pptx"),
            render=False,
            required_items=["1,180 weekly crossings"],
        )
        assert report["status"] == "pass", report["issues"]
        bad = fns["build_deck_from_markdown"](
            markdown="", output_path=str(tmp_path / "x.pptx")
        )
        assert bad["status"] == "error"
