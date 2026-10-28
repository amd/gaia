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
        is False
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


def test_a_one_slide_markdown_build_is_one_slide(tools, tmp_path):
    skill, fns, _r = tools
    md = "# Ferry plan\n\n## Ferry service grows to a network\n- Four piers\n"
    with bind(_Host(), skill):
        r = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "one.pptx"), expected_slides=1
        )
    assert r["status"] == "ok", r
    assert r["slides"] == 1


def test_the_procedure_checks_a_fresh_build_once(tools, tmp_path):
    """The build already runs the check; a second verify_deck is a wasted model turn."""
    skill, fns, _r = tools
    body = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "The build result's `verify` is the check" in body
    assert "only after\n   `add_slide` or `update_slide`" in body
    with bind(_Host(), skill):
        r = fns["build_deck_from_markdown"](
            markdown="## Ferry service grows to a network\n- Four piers\n",
            output_path=str(tmp_path / "f.pptx"),
            expected_slides=1,
        )
    assert r["verify"]["status"] == "pass"


def test_a_deck_with_no_folder_saves_in_gaia_documents(tools, tmp_path):
    skill, fns, _r = tools
    md = (
        "# Ferry plan\n\n## Crossings rose after the timetable change\n- 1,180 a week\n"
    )
    documents = tmp_path / "home" / "documents"
    with bind(_Host(), skill):
        unnamed = fns["build_deck_from_markdown"](markdown=md)
        named = fns["build_deck_from_markdown"](markdown=md, output_path="ferry.pptx")
    assert unnamed["path"] == str(documents / "Ferry plan.pptx")
    assert named["path"] == str(documents / "ferry.pptx")
    assert (documents / "Ferry plan.pptx").is_file()
    assert (documents / "ferry.pptx").is_file()


def test_an_unchanged_rebuild_of_a_deck_that_failed_its_check_is_refused(
    tools, tmp_path
):
    skill, fns, _r = tools
    md = "## Ferry crossings rose\n- 1,180 a week\n"
    out = str(tmp_path / "ferry.pptx")
    with bind(_Host(), skill):
        first = fns["build_deck_from_markdown"](
            markdown=md, output_path=out, required_items=["Pier repair finishes in May"]
        )
        again = fns["build_deck_from_markdown"](
            markdown=md, output_path=out, required_items=["Pier repair finishes in May"]
        )
        fixed = fns["build_deck_from_markdown"](
            markdown=md + "- Pier repair finishes in May\n",
            output_path=out,
            required_items=["Pier repair finishes in May"],
        )
    assert (
        first["status"] == "built_with_issues"
        and "Never call it verified" in first["next"]
    )
    assert again["status"] == "error"
    assert any("Pier repair" in p for p in again["problems"])
    assert fixed["status"] == "ok"


def test_a_build_says_when_to_stop(tools, tmp_path):
    skill, fns, _r = tools
    md = "# Ferry plan\n\n## Ferry crossings rose\n- 1,180 a week\n"
    must = ["Pier repair finishes in May"]
    with bind(_Host(), skill):
        passed = fns["build_deck_from_markdown"](markdown=md)
        first = fns["build_deck_from_markdown"](
            markdown=md + "- a\n", required_items=must
        )
        second = fns["build_deck_from_markdown"](
            markdown=md + "- b\n", required_items=must
        )
    assert "Report it to the user now" in passed["next"]
    assert first["next"].startswith("The deck failed its check")
    assert second["next"].startswith("Stop building")
    assert passed["path"] == first["path"] == second["path"]


def test_a_null_output_path_saves_in_gaia_documents(tools, tmp_path):
    skill, fns, _r = tools
    with bind(_Host(), skill):
        r = fns["build_deck_from_markdown"](
            markdown="# Plan\n\n## Crossings rose\n- 1,180 a week\n", output_path=None
        )
    assert r["status"] == "ok", r
    assert r["path"].startswith(str(tmp_path / "home" / "documents"))


_STAGES = (
    "## Ferry service grows from one pier to a network\n"
    "Diagram: process\n"
    "### One pier\n- Hourly crossing\n"
    "### Two piers\n- Half-hourly crossings\n"
    "### Network\n- Four piers, every ten minutes\n"
)
_SPLIT = (
    "# Ferry plan\n\n" + _STAGES + "\n## What grows at each stage\n"
    "| Dimension | One pier | Two piers | Network |\n"
    "|---|---|---|---|\n"
    "| Boats | 1 | 2 | 6 |\n"
    "Source: ferry_fleet.xlsx, 2026\n"
)


class _Asking(_Host):
    """A stand-in agent answering *request*, as Agent keeps it in _current_query."""

    def __init__(self, request: str = "") -> None:
        self._current_query = request


_ONE_SLIDE = "Make a crisp one-slide framework of how the ferry service grows."


def test_a_count_error_is_not_cleared_by_changing_the_count(tools, tmp_path):
    """A one-slide request built as three slides must be merged, not re-counted."""
    skill, fns, _r = tools
    out = str(tmp_path / "ferry.pptx")
    with bind(_Asking(_ONE_SLIDE), skill):
        first = fns["build_deck_from_markdown"](
            markdown=_SPLIT, output_path=out, expected_slides=1
        )
        raised = fns["build_deck_from_markdown"](
            markdown=_SPLIT, output_path=out, expected_slides=2
        )
        dropped = fns["build_deck_from_markdown"](
            markdown=_SPLIT, output_path=out, expected_slides=0
        )
        merged = fns["build_deck_from_markdown"](
            markdown=_STAGES + "Axis: More piers, boats and crossings\n",
            output_path=out,
            expected_slides=1,
        )
    assert first["status"] == "error"
    assert "'Axis:' line" in first["problems"][0]
    assert raised["status"] == "error"
    assert '("one-slide")' in raised["problems"][0]
    assert dropped["status"] == "error", "0 must apply the user's count, not skip it"
    assert merged["status"] == "ok", merged
    assert merged["slides"] == 1
    assert merged["slides_built"] == [
        "1. Ferry service grows from one pier to a network [diagram: process]"
    ]


def test_the_models_first_guess_never_overrides_the_users_count(tools, tmp_path):
    """The model misread a one-slide request as five; the user's words win."""
    skill, fns, _r = tools
    with bind(_Asking(_ONE_SLIDE), skill):
        guess = fns["build_deck_from_markdown"](
            markdown=_STAGES, output_path=str(tmp_path / "g.pptx"), expected_slides=5
        )
        right = fns["build_deck_from_markdown"](
            markdown=_STAGES, output_path=str(tmp_path / "r.pptx"), expected_slides=1
        )
    assert guess["status"] == "error"
    assert "not 5" in guess["problems"][0]
    assert right["status"] == "ok", right


def test_without_a_count_in_the_request_the_model_chooses(tools, tmp_path):
    skill, fns, _r = tools
    with bind(_Asking("Make a deck about the ferry service."), skill):
        two = fns["build_deck_from_markdown"](
            markdown=_SPLIT, output_path=str(tmp_path / "b.pptx"), expected_slides=2
        )
    assert two["status"] == "ok", two
    assert [line.split(" [")[0] for line in two["slides_built"]] == [
        "1. Ferry service grows from one pier to a network",
        "2. What grows at each stage",
    ]


def test_verify_applies_the_users_count(tools, tmp_path):
    skill, fns, _r = tools
    out = str(tmp_path / "ferry.pptx")
    with bind(_Asking(_ONE_SLIDE), skill):
        built = fns["build_deck_from_markdown"](markdown=_STAGES, output_path=out)
        report = fns["verify_deck"](path=out, expected_slides=3, render=False)
    assert built["status"] == "ok", built
    assert report["status"] == "error"
    assert "not 3" in report["problems"][0]


@pytest.mark.parametrize(
    "request_text, count",
    [
        ("a crisp one-slide framework ... a single executive slide", 1),
        ("Make a 5-slide deck on Q3", 5),
        ("Build a deck with three PowerPoint slides", 3),
        ("Summarize one of the slides", 0),
        ("make a slide deck about our roadmap", 0),
        ("add two more slides", 0),
        ("turn slide 3 into a chart", 0),
        ("a 2 slide summary and 4 slides of appendix", 0),
    ],
)
def test_the_count_comes_from_the_users_words(tools, request_text, count):
    import tools as skill_tools  # the skill's tools.py, on sys.path via conftest

    assert skill_tools._asked_count(request_text)[0] == count


def test_skill_call_request_is_the_users_message(tools):
    skill, _fns, _r = tools
    with bind(_Asking("one slide please"), skill) as call:
        assert call.request == "one slide please"
    with bind(_Host(), skill) as call:
        assert call.request == ""


def test_an_axis_line_draws_the_boxes_as_stages(tools, tmp_path):
    """Axis: names what grows along stages, so boxes with one become a process."""
    skill, fns, _r = tools
    boxes = _STAGES.replace("Diagram: process\n", "") + "Axis: More piers and boats\n"
    with bind(_Asking(), skill):
        r = fns["build_deck_from_markdown"](
            markdown=boxes, output_path=str(tmp_path / "f.pptx"), expected_slides=1
        )
    assert r["status"] == "ok", r
    assert r["slides_built"][0].endswith("[diagram: process]")


def test_an_axis_on_a_diagram_that_cannot_draw_it_is_an_error(tools, tmp_path):
    """A cycle ignores Axis:, so the dimensions it lists would vanish from the slide."""
    skill, fns, _r = tools
    cycle = _STAGES.replace("process", "cycle") + "Axis: More piers and boats\n"
    with bind(_Asking(), skill):
        r = fns["build_deck_from_markdown"](
            markdown=cycle, output_path=str(tmp_path / "c.pptx"), expected_slides=1
        )
    assert r["status"] == "error"
    assert "Diagram: process" in r["problems"][0]


def test_an_axis_under_more_than_six_steps_is_an_error(tools, tmp_path):
    skill, fns, _r = tools
    steps = "".join(f"### Step {i}\n- Detail {i}\n" for i in range(1, 8))
    md = f"## Seven steps to launch\nDiagram: process\n{steps}Axis: More effort\n"
    with bind(_Asking(), skill):
        r = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "s.pptx"), expected_slides=1
        )
    assert r["status"] == "error"
    assert "6 steps at most" in r["problems"][0]


_GROWS = (
    "One slide of the ferry stages. From left to right, increase: boats, crossings."
)


def test_a_left_to_right_progression_needs_its_axis_line(tools, tmp_path):
    """The user's 'from left to right, increase ...' must be the Axis, not a box's text."""
    skill, fns, _r = tools
    out = str(tmp_path / "ferry.pptx")
    with bind(_Asking(_GROWS), skill):
        buried = fns["build_deck_from_markdown"](
            markdown=_STAGES + "**More boats and crossings**\n",
            output_path=out,
            expected_slides=1,
        )
        drawn = fns["build_deck_from_markdown"](
            markdown=_STAGES + "Axis: More boats and crossings\n",
            output_path=out,
            expected_slides=1,
        )
    assert buried["status"] == "error"
    assert "'Axis:' line" in buried["problems"][0]
    assert drawn["status"] == "ok", drawn


def test_no_axis_is_needed_without_a_left_to_right_request(tools, tmp_path):
    skill, fns, _r = tools
    with bind(_Asking(_ONE_SLIDE), skill):
        r = fns["build_deck_from_markdown"](
            markdown=_STAGES, output_path=str(tmp_path / "f.pptx"), expected_slides=1
        )
    assert r["status"] == "ok", r


def test_an_axis_must_name_everything_the_user_listed(tools, tmp_path):
    """A copied example axis that drops the user's items is refused, naming them."""
    skill, fns, _r = tools
    request = (
        "One slide of the ferry stages. As we move from left to right, increase:\n"
        "\n* Boats in service\n* Crossings per hour\n* Crew on shift\n\nKeep it simple."
    )
    out = str(tmp_path / "ferry.pptx")
    with bind(_Asking(request), skill):
        partial = fns["build_deck_from_markdown"](
            markdown=_STAGES + "Axis: More boats\n", output_path=out
        )
        full = fns["build_deck_from_markdown"](
            markdown=_STAGES + "Axis: More boats, crossings and crew\n", output_path=out
        )
    assert partial["status"] == "error"
    assert "Crossings per hour; Crew on shift" in partial["problems"][0]
    assert full["status"] == "ok", full


def test_boxes_on_a_chart_slide_are_an_error_not_dropped(tools, tmp_path):
    """A chart slide has no room for '### ' boxes; they must not vanish silently."""
    skill, fns, _r = tools
    md = (
        "## Ferry demand rose at every pier\n"
        "```chart bar\nsource: ferry_counts.xlsx, 2026\nPier,Crossings\nNorth,120\nSouth,90\n```\n"
        "### North pier\n- Hourly crossing\n### South pier\n- Half-hourly\n"
    )
    with bind(_Asking(), skill):
        r = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "c.pptx")
        )
    assert r["status"] == "error"
    assert "(North pier, South pier) would not appear" in r["problems"][0]


def test_a_chart_alone_cannot_drop_what_the_user_listed(tools, tmp_path):
    skill, fns, _r = tools
    request = (
        "One slide of the ferry stages. As we move from left to right, increase:\n"
        "\n* Boats in service\n* Crew on shift\n"
    )
    md = (
        "## Ferry crossings grow at every stage\n"
        "```chart bar\nsource: ferry_counts.xlsx, 2026\nStage,Crossings\n"
        "One pier,10\nNetwork,60\n```\n"
    )
    with bind(_Asking(request), skill):
        r = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "c.pptx")
        )
    assert r["status"] == "error"
    assert "Boats in service; Crew on shift" in r["problems"][0]


@pytest.mark.parametrize(
    "request_text, listed",
    [
        (
            "As we move from left to right, increase:\n\n* Task complexity\n* Compute\n",
            ["Task complexity", "Compute"],
        ),
        (
            "Across the tiers these grow: cost, staff and hours.",
            ["cost", "staff", "hours"],
        ),
        ("From step to step, more of: risk, review effort", ["risk", "review effort"]),
        ("Lay the three options out left to right.", []),
        ("Revenue should increase: we need a deck on it.", []),
        ("Show what grows across the stages: cost", []),
        ("One slide: the stages; from left to right, increase boats and crew.", []),
    ],
)
def test_what_grows_comes_only_from_an_explicit_list(tools, request_text, listed):
    import tools as skill_tools

    assert skill_tools._grows_list(request_text) == listed


def test_content_under_the_only_title_is_the_slide_not_a_cover(tools, tmp_path):
    """'# Title' over KPIs with no '## ' slide used to become a cover with no KPIs."""
    skill, fns, _r = tools
    md = "# October KPIs\n\n- Revenue: $12.4M\n- Churn: 2.1%\n- NPS: 48\n"
    with bind(_Asking("One slide with October KPIs."), skill):
        r = fns["build_deck_from_markdown"](
            markdown=md,
            output_path=str(tmp_path / "k.pptx"),
            required_items=["$12.4M", "2.1%", "48"],
        )
    assert r["status"] == "ok", r
    assert r["slides"] == 1


def test_content_under_a_real_cover_is_an_error_not_dropped(tools, tmp_path):
    skill, fns, _r = tools
    md = "# Ferry plan\n- Crossings up 20%\n\n## Next steps\n- Order a boat\n"
    with bind(_Asking(), skill):
        r = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "c.pptx")
        )
    assert r["status"] == "error"
    assert "Crossings up 20%" in r["problems"][0]


_CHURN = (
    "## Churn fell every quarter\n"
    "```chart line\nsource: {src}\nQuarter,Churn %\nQ1,3.4\nQ2,2.9\nQ3,2.1\n```\n"
)


def test_made_up_numbers_must_be_labelled_on_the_slide(tools, tmp_path):
    """Invented data the user invited is fine; presenting it as real is not."""
    skill, fns, _r = tools
    out = str(tmp_path / "churn.pptx")
    with bind(_Asking("One slide on churn going down. Make up some numbers."), skill):
        bare = fns["build_deck_from_markdown"](
            markdown=_CHURN.format(src="CRM export, 2026"), output_path=out
        )
        labelled = fns["build_deck_from_markdown"](
            markdown=_CHURN.format(src="Illustrative data, not actual results"),
            output_path=out,
        )
    assert bare["status"] == "error"
    assert "Illustrative data" in bare["problems"][0]
    assert labelled["status"] == "ok", labelled


@pytest.mark.parametrize(
    "request_text, invites",
    [
        ("Make up some realistic numbers", True),
        ("use dummy data for now", True),
        ("a hypothetical scenario with sample numbers", True),
        ("Make a slide about our Q3 revenue of $5.6M", False),
        ("make sure it looks clean", False),
    ],
)
def test_only_an_invitation_to_invent_numbers_triggers_the_label(
    tools, request_text, invites
):
    import tools as skill_tools

    assert bool(skill_tools._INVENTED.search(request_text)) is invites
