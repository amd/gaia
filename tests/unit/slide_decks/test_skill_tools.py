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

import json
from pathlib import Path

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
    "export_slide_image",
}


class _Host:
    """Enough of an agent for gaia.skills.context: not private, no validator."""

    _incognito = False


@pytest.fixture
def tools(monkeypatch, tmp_path):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    # A build with no output_path saves in the working folder, then the user's
    # Documents: both are redirected so no test writes outside tmp_path.
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "userhome"))
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


def test_without_a_count_in_the_request_the_model_chooses(tools, tmp_path):
    skill, fns, _r = tools
    with bind(_Asking("Make a deck about the ferry service: 1, 2 and 6 boats."), skill):
        two = fns["build_deck_from_markdown"](
            markdown=_SPLIT, output_path=str(tmp_path / "b.pptx"), expected_slides=2
        )
    assert two["status"] == "ok", two
    assert [line.split(" [")[0] for line in two["slides_built"]] == [
        "1. Ferry service grows from one pier to a network",
        "2. What grows at each stage",
    ]


def test_the_request_is_the_users_message(tools):
    import sys

    skill, fns, _r = tools
    # The module the loader gave this skill's tools.py: never a bare `tools` import,
    # which another skill's tools.py on sys.path could answer.
    skill_tools = sys.modules[fns["build_deck"].__module__]
    with bind(_Asking("one slide please"), skill) as call:
        assert skill_tools._request(call) == "one slide please"
    with bind(_Host(), skill) as call:
        assert skill_tools._request(call) == ""


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


def test_content_under_the_only_title_is_the_slide_not_a_cover(tools, tmp_path):
    """'# Title' over KPIs with no '## ' slide used to become a cover with no KPIs."""
    skill, fns, _r = tools
    md = "# October KPIs\n\n- Revenue: $12.4M\n- Churn: 2.1%\n- NPS: 48\n"
    with bind(
        _Asking("One slide with October KPIs: $12.4M revenue, 2.1% churn, NPS 48."),
        skill,
    ):
        r = fns["build_deck_from_markdown"](
            markdown=md,
            output_path=str(tmp_path / "k.pptx"),
            required_items=["$12.4M", "2.1%", "48"],
        )
    assert r["status"] == "ok", r
    assert r["slides"] == 1


_CHURN = (
    "## Churn fell every quarter\n"
    "```chart line\nsource: {src}\nQuarter,Churn %\nQ1,3.4\nQ2,2.9\nQ3,2.1\n```\n"
)


def test_a_chart_type_on_its_own_line_is_read(tools, tmp_path):
    """'```chart' then 'bar' on the next line used to fail as a chart with no data."""
    skill, fns, _r = tools
    md = (
        "## West led every quarter\n"
        "```chart\nbar\nsource: finance_q3.xlsx, 2026\nRegion,Revenue\nWest,5.6\nEast,4.1\n```\n"
    )
    with bind(_Asking(), skill):
        r = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "c.pptx")
        )
    assert r["status"] == "ok", r


class _Seeing(_Asking):
    """A stand-in agent that has also seen tool results, as Agent.seen_sources keeps."""

    def __init__(self, request: str, seen=()) -> None:
        super().__init__(request)
        self._seen = [("user", {}, request), *seen]

    def seen_sources(self):
        return list(self._seen)


_REGIONS = "Q3 revenue in $M: West 5.6, East 4.1, North 3.2. Two slides."


def _share_slide(claim: str) -> str:
    return (
        f"## West drove {claim} of Q3 revenue\n"
        "```chart bar\nsource: finance_q3.xlsx\nRegion,Revenue ($M)\n"
        "West,5.6\nEast,4.1\nNorth,3.2\n```\n"
        "## Next steps\n- Hire sales reps in North\n"
    )


def test_a_number_a_tool_computed_is_grounded_even_rounded(tools, tmp_path):
    skill, fns, _r = tools
    computed = ("run_python", {"code": "5.6/12.9"}, '{"stdout": "0.4341085"}')
    with bind(_Seeing(_REGIONS, [computed]), skill):
        r = fns["build_deck_from_markdown"](
            markdown=_share_slide("43%"), output_path=str(tmp_path / "s.pptx")
        )
    assert r["status"] == "ok", r


def test_a_number_from_a_file_read_is_grounded(tools, tmp_path):
    skill, fns, _r = tools
    read = ("read_file", {"path": "kpis.txt"}, "NPS this quarter: 48")
    md = "## NPS reached 48 this quarter\n- Up from last quarter\n"
    with bind(_Seeing("One slide on our NPS from kpis.txt.", [read]), skill):
        r = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "n.pptx")
        )
    assert r["status"] == "ok", r


def test_a_count_of_the_slides_own_parts_in_its_title_is_fine(tools, tmp_path):
    skill, fns, _r = tools
    md = _STAGES.replace(
        "## Ferry service grows from one pier to a network",
        "## 3 stages from one pier to a network",
    )
    with bind(_Seeing("One slide: the ferry stages."), skill):
        r = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "c.pptx")
        )
    assert r["status"] == "ok", r


def test_bold_in_a_table_cell_is_bold_not_asterisks(out):
    from pptx import Presentation
    from slidedeck.build import build_deck
    from slidedeck.markdown import outline_from_markdown

    md = "## Model B is the pick\n| Model | RAM |\n|---|---|\n| A | 16 GB |\n| B | **32 GB** |\n"
    path = out("t.pptx")
    assert build_deck(outline_from_markdown(md), path)["status"] in (
        "ok",
        "built_with_issues",
    )
    table = next(s for s in Presentation(path).slides[0].shapes if s.has_table).table
    cell = table.cell(2, 1).text_frame.paragraphs[0]
    assert cell.text == "32 GB" and all(r.font.bold for r in cell.runs)


def test_export_slide_image_saves_a_png_by_default_and_jpeg_on_request(
    tools, tmp_path, soffice, monkeypatch
):
    import tempfile

    # LibreOffice's profile path must stay short; pytest's tmp_path is not.
    short = tempfile.mkdtemp(prefix="g")
    monkeypatch.setenv("GAIA_HOME", short)
    skill, fns, _r = tools
    deck = str(tmp_path / "s.pptx")
    with bind(_Asking(), skill):
        built = fns["build_deck_from_markdown"](markdown=_STAGES, output_path=deck)
        png = fns["export_slide_image"](deck_path=deck)
        jpg = fns["export_slide_image"](
            deck_path=deck, image_format="jpeg", output_path=str(tmp_path / "s.jpg")
        )
        bad = fns["export_slide_image"](deck_path=deck, image_format="gif")
        missing = fns["export_slide_image"](deck_path=deck, slide=4)
    assert built["status"] == "ok", built
    assert png["status"] == "ok", png
    p = png["images"][0]["path"]
    assert p.endswith(".png") and open(p, "rb").read(8) == b"\x89PNG\r\n\x1a\n"
    assert png["images"][0]["width"] == 1920
    assert (
        jpg["status"] == "ok"
        and open(jpg["images"][0]["path"], "rb").read(3) == b"\xff\xd8\xff"
    )
    assert bad["status"] == "error" and "png" in bad["problems"][0]
    assert missing["status"] == "error" and "has 1" in missing["problems"][0]
    assert "slide=1" in missing["problems"][0]


_FUNNEL = (
    "One slide on our October funnel: 20,000 visited signup, 6,400 created an "
    "account, 1,600 were active after 7 days."
)


def test_arithmetic_on_the_users_own_numbers_needs_no_tool(tools, tmp_path):
    """13,600 lost (68%) is the user's numbers subtracted and divided, not invented."""
    skill, fns, _r = tools
    md = (
        "## Signup loses 13,600 visitors (68%)\n"
        "- 20,000 visited, 6,400 created an account\n- 8% were active after 7 days\n"
    )
    with bind(_Seeing(_FUNNEL), skill):
        r = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "f.pptx")
        )
    assert r["status"] == "ok", r


def test_model_shapes_that_used_to_cost_rebuilds_now_build_first_time(tools, tmp_path):
    """Real first attempts from stress runs that the skill used to refuse or garble."""
    skill, fns, _r = tools
    outline = {
        "slides": [
            {
                "type": "diagram",
                "diagram": "boxes",
                "title": "Ferry service grows in three stages",
                "axis": "More boats",
                "items": [
                    "One pier\n\nHourly crossing",
                    "Two piers\n\nHalf-hourly",
                    "Network\n\nEvery ten minutes",
                ],
            }
        ]
    }
    md = (
        "# Ferry plan\n\n## Three stages\n### One pier\nHourly crossing\nOne boat\n"
        "### Two piers\nHalf-hourly\nTwo boats\n### Network\nEvery ten minutes\nSix boats\n"
        "\n---\n\n# Recommendation\n- Order two boats\n"
    )
    with bind(_Asking(), skill):
        j = fns["build_deck"](outline=outline, output_path=str(tmp_path / "j.pptx"))
        m = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "m.pptx")
        )
    assert j["status"] == "ok", j
    assert j["slides_built"] == [
        "1. Ferry service grows in three stages [diagram: process]"
    ]
    assert m["status"] == "ok", m
    assert [x.split(" [")[0] for x in m["slides_built"]] == [
        "1. Ferry plan",
        "2. Three stages",
        "3. Recommendation",
    ]


def test_a_chart_of_the_users_own_figures_needs_no_made_up_source(tools, tmp_path):
    """The user typed the numbers, so there is no file to cite and nothing to invent."""
    skill, fns, _r = tools
    md = "## Churn fell every quarter\n```chart line\nQuarter,Churn %\nQ1,3.4\nQ2,2.9\nQ3,2.1\n```\n"
    with bind(
        _Asking("One slide: churn was 3.4% in Q1, 2.9% in Q2, 2.1% in Q3."), skill
    ):
        r = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "u.pptx")
        )
    assert r["status"] == "ok", r
    assert not [i for i in r["verify"]["issues"] if i["check"] == "missing_source"]


def test_a_chart_of_other_figures_still_needs_a_source(tools, tmp_path):
    skill, fns, _r = tools
    md = "## Churn fell every quarter\n```chart line\nQuarter,Churn %\nQ1,3.4\nQ2,2.9\nQ3,2.1\n```\n"
    with bind(_Host(), skill):
        r = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "f.pptx")
        )
    assert [i for i in r["verify"]["issues"] if i["check"] == "missing_source"]


def test_phase_and_step_labels_are_not_figures(tools, tmp_path):
    skill, fns, _r = tools
    md = (
        "## Rollout plan\n\n### Phase 1\n- Pilot one depot\n\n"
        "### Phase 2\n- Two regions\n\n### Phase 3\n- Every depot\n"
    )
    with bind(_Asking("One slide on the depot rollout plan in phases."), skill):
        r = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "p.pptx")
        )
    assert r["status"] == "ok", r


def test_one_box_beside_a_table_becomes_its_takeaway(tools, tmp_path):
    skill, fns, _r = tools
    md = (
        "## West leads on revenue\n\n| Region | Revenue |\n|---|---|\n"
        "| West | 4.2 |\n| East | 3.1 |\n\n### Key insight\nKeep funding West\n"
    )
    with bind(_Asking("One slide: West 4.2, East 3.1 revenue."), skill):
        r = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "t.pptx")
        )
    assert r["status"] == "ok", r
    assert "Keep funding West" in json.dumps(r["slides_built"])


def test_a_new_chat_never_overwrites_another_chats_deck(tools, tmp_path):
    """Same title in a new chat: a fresh file, not an overwrite prompt."""
    skill, fns, _r = tools
    md = "## Ferry plan\n- Order a boat\n- Hire crew\n"
    with bind(_Asking("one slide on the ferry plan"), skill):
        first = fns["build_deck_from_markdown"](markdown=md)
        again = fns["build_deck_from_markdown"](markdown=md)
    with bind(_Asking("one slide on the ferry plan"), skill):
        other = fns["build_deck_from_markdown"](markdown=md)
    assert first["status"] == again["status"] == other["status"] == "ok", other
    assert first["path"] == again["path"]
    assert other["path"] != first["path"]
    assert other["path"].endswith("(2).pptx")
    assert any("already exists" in n for n in other.get("notes") or [])
    assert Path(first["path"]).parent == tmp_path / "work"


def test_status_words_colour_boxes_and_cards_fit_their_text(tools, tmp_path):
    from pptx import Presentation
    from pptx.util import Inches

    skill, fns, _r = tools
    md = (
        "## Weekly status\n\n### Backend — Green\n- Done\n\n"
        "### Connector — Amber\n- Fix in review\n\n### Mobile UI — Red\n- Slipping\n"
    )
    out = tmp_path / "s.pptx"
    with bind(_Asking("one slide weekly status"), skill):
        r = fns["build_deck_from_markdown"](markdown=md, output_path=str(out))
    assert r["status"] == "ok", r
    boxes = [
        s for s in Presentation(str(out)).slides[0].shapes if s.name.startswith("Box ")
    ]
    fills = {str(b.fill.fore_color.rgb) for b in boxes}
    assert len(fills) == 3
    assert all(b.height < Inches(3) for b in boxes)


def test_several_points_beside_a_table_become_a_side_panel(tools, tmp_path):
    skill, fns, _r = tools
    md = (
        "## Build vs license\n\n| | Build | License |\n|---|---|---|\n"
        "| Time | 9 months | 6 weeks |\n| Cost | 4 engineers | $0.004/min |\n\n"
        "### Recommendation: license now\n- Faster to market\n- Cost is manageable\n"
        "- Revisit in 12 months\n"
    )
    with bind(
        _Asking(
            "One slide: build takes 9 months with 4 engineers; license takes 6 weeks at $0.004/min; revisit in 12 months."
        ),
        skill,
    ):
        r = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "d.pptx")
        )
    assert r["status"] == "ok", r


def test_italic_markers_do_not_show_on_the_slide(tools, tmp_path):
    skill, fns, _r = tools
    md = '## What we heard\n\n- *"I gave up on day two"*\n- **Setup** is _slow_\n'
    with bind(_Asking("one slide on what we heard"), skill):
        r = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "i.pptx")
        )
    assert r["status"] == "ok", r
    from pptx import Presentation

    text = " ".join(
        sh.text_frame.text
        for sh in Presentation(r["path"]).slides[0].shapes
        if sh.has_text_frame
    )
    assert '"I gave up on day two"' in text and "*" not in text and "_slow_" not in text


def test_the_option_a_decision_picks_stands_out_in_its_column(tools, tmp_path):
    from pptx import Presentation

    skill, fns, _r = tools
    md = (
        "## License the speech model now\n\n| | Build in-house | License vendor |\n"
        "|---|---|---|\n| Time | 9 months | 6 weeks |\n| Control | Full | Vendor lock-in |\n"
        "\nTakeaway: License now, revisit building in 12 months\n"
    )
    out = tmp_path / "d.pptx"
    with bind(
        _Asking(
            "One slide: build takes 9 months, license takes 6 weeks; revisit in 12 months."
        ),
        skill,
    ):
        r = fns["build_deck_from_markdown"](markdown=md, output_path=str(out))
    assert r["status"] == "ok", r
    table = next(
        sh.table for sh in Presentation(str(out)).slides[0].shapes if sh.has_table
    )
    picked = {str(table.cell(i, 2).fill.fore_color.rgb) for i in (1, 2)}
    other = {str(table.cell(i, 1).fill.fore_color.rgb) for i in (1, 2)}
    assert len(picked) == 1 and not picked & other
    assert all(r.font.bold for r in table.cell(1, 2).text_frame.paragraphs[0].runs)


def test_table_numbers_keep_the_separators_the_user_typed(tools, tmp_path):
    from pptx import Presentation

    skill, fns, _r = tools
    md = (
        "## Signup is the biggest leak\n\n| Stage | Users | Year |\n|---|---|---|\n"
        "| Visited | 20,000 | 2026 |\n| Signed up | 6,400 | 2026 |\n\n"
        "### Biggest drop: signup\nTwo in three visitors never sign up\n"
    )
    with bind(_Asking("One slide: 20,000 visited, 6,400 signed up, in 2026."), skill):
        r = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "f.pptx")
        )
    assert r["status"] == "ok", r
    shapes = Presentation(r["path"]).slides[0].shapes
    cells = [
        c.text
        for sh in shapes
        if sh.has_table
        for row in sh.table.rows
        for c in row.cells
    ]
    assert "6,400" in cells and "20,000" in cells and "2026" in cells
    text = json.dumps(r["slides_built"])
    assert "Biggest drop: signup. Two in three" in text


def test_a_rebuild_replaces_its_own_deck_even_after_the_skill_reloads(tools, tmp_path):
    """Ownership is stamped in the file, so reloading the skill loses nothing."""
    from gaia.skills.loader import register_skill_tools

    skill, fns, _r = tools
    md = "## Ferry plan\n- Order a boat\n- Hire crew\n"
    with bind(_Asking("one slide on the ferry plan"), skill):
        theirs = fns["build_deck_from_markdown"](markdown=md)
    me = _Asking("one slide on the ferry plan")
    with bind(me, skill):
        first = fns["build_deck_from_markdown"](markdown=md)
    reloaded = {
        k.split("/", 1)[1]: v["function"]
        for k, v in register_skill_tools(skill).items()
    }
    with bind(me, skill):
        again = reloaded["build_deck_from_markdown"](markdown=md)
    assert first["path"] != theirs["path"]
    assert again["path"] == first["path"], (first["path"], again["path"])


def test_a_quote_beside_points_is_a_panel_quoted_once_and_a_lone_line_is_a_statement(
    tools, tmp_path
):
    from pptx import Presentation

    skill, fns, _r = tools
    md = (
        "# Interviews\n\n## Big shift ahead\nLess clicking, more deciding.\n\n"
        "## Setup is the top pain\n- Setup is slow\n- Calendar wanted\n- Privacy worries\n"
        '\n> *"I gave up on day two"*\n'
    )
    with bind(_Asking("A 2-slide summary of the interviews."), skill):
        r = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "q.pptx"), expected_slides=2
        )
    assert r["status"] == "ok", r
    assert "[section]" in r["slides_built"][0]
    shapes = {s.name: s for s in Presentation(r["path"]).slides[1].shapes}
    assert shapes["Pull quote"].text_frame.text == "“I gave up on day two”"
    body = next(
        s
        for n, s in shapes.items()
        if "Pull quote" not in n
        and s.has_text_frame
        and "Setup is slow" in s.text_frame.text
    )
    assert body.top > 0 and body.left + body.width <= shapes["Quote panel"].left


# --------------------------------------------------------------------------
# A build makes the deck whenever there is a slide; findings are warnings
# --------------------------------------------------------------------------


def _texts(path) -> str:
    """Every word on every slide of the deck at *path*."""
    from pptx import Presentation

    found = []
    for slide in Presentation(str(path)).slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                found.append(shape.text_frame.text)
            if getattr(shape, "has_table", False) and shape.has_table:
                found += [c.text for row in shape.table.rows for c in row.cells]
    return "\n".join(found)


def _count(path) -> int:
    from pptx import Presentation

    return len(Presentation(str(path)).slides)


def test_only_what_makes_a_file_impossible_is_an_error(tools, tmp_path):
    skill, fns, _r = tools
    one = {"slides": [{"type": "title", "title": "x"}]}
    with bind(_Host(), skill):
        empty = fns["build_deck"](
            outline={"slides": []}, output_path=str(tmp_path / "x.pptx")
        )
        no_markdown = fns["build_deck_from_markdown"](
            markdown="", output_path=str(tmp_path / "x.pptx")
        )
        in_skill = fns["build_deck"](outline=one, output_path=str(SKILL_DIR / "x.pptx"))
        missing = fns["verify_deck"](path=str(tmp_path / "missing.pptx"))
        blueprint = fns["outline_from_blueprint"](name="no_such_blueprint")
        too_long = fns["outline_from_blueprint"](name="project_status", slide_count=99)
        assert fns["deck_outline_from_markdown"](markdown="")["status"] == "error"
        assert (
            fns["update_deck_prefs"](changes={"density": "huge"})["status"] == "error"
        )
    assert empty["status"] == "error"
    assert no_markdown["status"] == "error"
    assert "build_deck_from_markdown(markdown=" in no_markdown["problems"][0]
    assert in_skill["status"] == "error" and "output_path=''" in in_skill["error"]
    assert not (SKILL_DIR / "x.pptx").exists()
    assert missing["status"] == "error"
    assert "build_deck_from_markdown(markdown=" in missing["error"]
    assert blueprint["status"] == "error" and "list_blueprints" in blueprint["error"]
    assert "outline_from_blueprint(name='project_status', slide_count=" in (
        too_long["error"]
    )


def test_a_wrong_extension_or_a_missing_folder_still_saves_a_deck(tools, tmp_path):
    skill, fns, _r = tools
    one = {"slides": [{"type": "title", "title": "x"}]}
    with bind(_Host(), skill):
        docx = fns["build_deck"](outline=one, output_path=str(tmp_path / "x.docx"))
        bare = fns["build_deck"](outline=one, output_path=str(tmp_path / "v1.2 plan"))
        new_dir = fns["build_deck"](
            outline=one, output_path=str(tmp_path / "new" / "sub" / "x.pptx")
        )
    assert docx["status"] == "ok", docx
    assert docx["path"] == str(tmp_path / "x.pptx")
    assert any("'.docx'" in w and "x.pptx" in w for w in docx["warnings"])
    assert bare["path"] == str(tmp_path / "v1.2 plan.pptx")
    assert new_dir["status"] == "ok" and (tmp_path / "new" / "sub" / "x.pptx").is_file()


_SEVEN = "# Ferry plan\n\n" + "".join(
    f"## Point {i} of the ferry plan\n- Detail for point {i}\n" for i in range(1, 7)
)


def test_a_one_slide_request_builds_in_one_call_whatever_was_sent(tools, tmp_path):
    """Seven slides against expected_slides=1 used to be refused, four times in a row."""
    skill, fns, _r = tools
    out = tmp_path / "seven.pptx"
    with bind(_Asking("Make me one slide about the ferry plan."), skill):
        r = fns["build_deck_from_markdown"](
            markdown=_SEVEN, output_path=str(out), expected_slides=1
        )
    assert r["status"] == "ok", r
    assert r["slides"] == _count(out) == 7
    count = [w for w in r["warnings"] if "expected_slides was 1" in w]
    assert count and "The deck has 7 slides" in count[0]
    assert "build again with 1 '## ' slide" in count[0]
    assert "Point 6 of the ferry plan" in _texts(out)
    assert str(out) in r["next"]


def test_a_plain_one_slide_outline_is_one_slide_with_no_warning(tools, tmp_path):
    skill, fns, _r = tools
    out = tmp_path / "one.pptx"
    with bind(_Asking("Make me one slide about the ferry plan."), skill):
        r = fns["build_deck_from_markdown"](
            markdown="## Ferry service grows to a network\n- Four piers\n- Six boats",
            output_path=str(out),
            expected_slides=1,
        )
    assert r["status"] == "ok", r
    assert r["slides"] == _count(out) == 1
    assert r["warnings"] == []
    assert "Four piers" in _texts(out)


def test_a_cover_in_front_of_the_expected_count_is_left_out_and_said(tools, tmp_path):
    """expected_slides counts every slide, the cover included."""
    skill, fns, _r = tools
    md = "# Ferry plan\n\n## Ferry service grows to a network\n- Four piers\n"
    outline = {
        "slides": [
            {"type": "title", "title": "Ferry plan"},
            {"type": "bullets", "title": "Service grows", "bullets": ["Four piers"]},
        ]
    }
    with bind(_Host(), skill):
        m = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "m.pptx"), expected_slides=1
        )
        j = fns["build_deck"](
            outline=outline, output_path=str(tmp_path / "j.pptx"), expected_slides=1
        )
        kept = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(tmp_path / "k.pptx"), expected_slides=2
        )
    for r in (m, j):
        assert r["status"] == "ok" and r["slides"] == 1, r
        said = [w for w in r["warnings"] if "cover" in w]
        assert said and "'Ferry plan'" in said[0] and "expected_slides=2" in said[0]
    assert kept["slides"] == 2 and kept["warnings"] == []


def test_too_few_slides_names_the_call_that_adds_one(tools, tmp_path):
    skill, fns, _r = tools
    out = tmp_path / "few.pptx"
    with bind(_Host(), skill):
        r = fns["build_deck_from_markdown"](
            markdown="## Ferry plan\n- Order a boat\n",
            output_path=str(out),
            expected_slides=3,
        )
        report = fns["verify_deck"](path=str(out), expected_slides=3, render=False)
    assert r["status"] == "ok" and r["slides"] == 1
    assert any(f"add_slide(deck_path='{out}'" in w for w in r["warnings"])
    count = [i for i in report["issues"] if i["check"] == "slide_count"]
    assert report["status"] == "pass" and count[0]["severity"] == "warning"


def test_the_request_is_never_parsed_for_a_count_or_a_layout(tools, tmp_path):
    """What is built depends on the arguments, not on how the user worded the request."""
    skill, fns, _r = tools
    built = []
    for i, request in enumerate(
        [
            "",
            "A crisp one-slide framework, a single executive slide.",
            "Make a 5-slide deck. From left to right, increase: boats, crossings.",
        ]
    ):
        with bind(_Asking(request), skill):
            r = fns["build_deck_from_markdown"](
                markdown=_SPLIT, output_path=str(tmp_path / f"r{i}.pptx")
            )
        assert r["status"] == "ok", r
        built.append((r["slides"], r["slides_built"], r["verify"]["status"]))
        assert not [w for w in r["warnings"] if "expected_slides" in w or "Axis" in w]
    assert built[0] == built[1] == built[2]
    assert built[0][0] == 3


def test_an_unknown_theme_builds_with_the_default_and_names_the_themes(tools, tmp_path):
    skill, fns, _r = tools
    out = tmp_path / "t.pptx"
    with bind(_Host(), skill):
        r = fns["build_deck_from_markdown"](
            markdown="## Ferry plan\n- Order a boat\n",
            output_path=str(out),
            theme="professional",
        )
        in_outline = fns["build_deck"](
            outline={"theme": "neon", "slides": [{"title": "Ferry plan"}]},
            output_path=str(tmp_path / "o.pptx"),
        )
        alias = fns["build_deck_from_markdown"](
            markdown="## Ferry plan\n- Order a boat\n",
            output_path=str(tmp_path / "d.pptx"),
            theme="Dark",
        )
    assert r["status"] == "ok" and out.is_file(), r
    assert r["theme"] == "light"
    said = [w for w in r["warnings"] if "'professional'" in w]
    assert said and "light, dark, minimal" in said[0] and "theme='light'" in said[0]
    assert in_outline["status"] == "ok" and in_outline["theme"] == "light"
    assert any("'neon'" in w for w in in_outline["warnings"])
    assert alias["theme"] == "dark" and alias["warnings"] == []


def test_a_style_setting_that_cannot_be_used_is_dropped_and_named(tools, tmp_path):
    skill, fns, _r = tools
    with bind(_Host(), skill):
        r = fns["build_deck_from_markdown"](
            markdown="## Ferry plan\n- Order a boat\n",
            output_path=str(tmp_path / "p.pptx"),
            prefs={"density": "huge", "sparkle": True, "theme": "dark"},
        )
        template = fns["build_deck"](
            outline={"slides": [{"title": "Ferry plan"}]},
            output_path=str(tmp_path / "q.pptx"),
            template_path=str(tmp_path / "no-such-template.potx"),
        )
    assert r["status"] == "ok" and r["theme"] == "dark", r
    assert any("density" in w and "ignored" in w for w in r["warnings"])
    assert any("sparkle" in w for w in r["warnings"])
    assert template["status"] == "ok", template
    assert any("does not exist" in w and "theme" in w for w in template["warnings"])


def test_a_diagram_too_long_for_its_axis_is_drawn_without_it(tools, tmp_path):
    skill, fns, _r = tools
    steps = "".join(f"### Step {i}\n- Detail {i}\n" for i in range(1, 12))
    md = f"## Eleven steps to launch\nDiagram: process\n{steps}Axis: More effort\n"
    out = tmp_path / "s.pptx"
    with bind(_Host(), skill):
        r = fns["build_deck_from_markdown"](markdown=md, output_path=str(out))
        cycle = fns["build_deck_from_markdown"](
            markdown=_STAGES.replace("process", "cycle") + "Axis: More piers\n",
            output_path=str(tmp_path / "c.pptx"),
        )
    assert r["status"] in ("ok", "built_with_issues") and _count(out) == 1, r
    assert "Step 11" in _texts(out)
    said = [w for w in r["warnings"] if "6 steps at most" in w]
    assert said and "has 11" in said[0] and "merge the steps" in said[0]
    assert cycle["status"] == "ok", cycle
    assert any("Diagram: process" in w for w in cycle["warnings"])


def test_a_slide_that_cannot_be_drawn_is_built_as_text_with_the_fix(tools, tmp_path):
    skill, fns, _r = tools
    out = tmp_path / "b.pptx"
    outline = {
        "slides": [
            {"type": "bullets", "title": "Ferry plan", "bullets": ["Order a boat"]},
            {
                "type": "chart",
                "title": "Crossings rose",
                "bullets": ["Up every month"],
            },
            {"type": "hologram", "title": "Fleet", "bullets": ["Six boats"]},
        ]
    }
    with bind(_Host(), skill):
        r = fns["build_deck"](outline=outline, output_path=str(out))
    assert r["status"] == "ok" and _count(out) == 3, r
    text = _texts(out)
    assert "Crossings rose" in text and "Up every month" in text and "Six boats" in text
    chart = [w for w in r["warnings"] if w.startswith("slide 2 (chart)")]
    assert chart and "update_slide(deck_path=" in chart[0]
    assert '"type": "chart"' in chart[0]
    assert any("'hologram'" in w for w in r["warnings"])


def test_markdown_that_used_to_be_refused_is_built_and_explained(tools, tmp_path):
    skill, fns, _r = tools
    cover = "# Ferry plan\n- Crossings up 20%\n\n## Next steps\n- Order a boat\n"
    boxes = (
        "## Ferry demand rose at every pier\n"
        "```chart bar\nsource: ferry_counts.xlsx, 2026\nPier,Crossings\nNorth,120\nSouth,90\n```\n"
        "### North pier\n- Hourly crossing\n### South pier\n- Half-hourly\n"
    )
    open_block = (
        "## Crossings by pier\n```chart bar\nsource: counts.xlsx\nPier,Crossings\n"
        "North,120\nSouth,90\n## Next steps\n- Order a boat\n"
    )
    bad_json = "## Fleet\n- Six boats\n```slide\n{not json\n```\n"
    with bind(_Host(), skill):
        c = fns["build_deck_from_markdown"](
            markdown=cover, output_path=str(tmp_path / "c.pptx")
        )
        b = fns["build_deck_from_markdown"](
            markdown=boxes, output_path=str(tmp_path / "b.pptx")
        )
        o = fns["build_deck_from_markdown"](
            markdown=open_block, output_path=str(tmp_path / "o.pptx")
        )
        j = fns["build_deck_from_markdown"](
            markdown=bad_json, output_path=str(tmp_path / "j.pptx")
        )
    assert c["status"] == "ok" and c["slides"] == 3, c
    assert "Crossings up 20%" in _texts(tmp_path / "c.pptx")
    assert any("cover held slide content" in w for w in c["warnings"])
    assert b["slides"] == 1 and "Half-hourly" in _texts(tmp_path / "b.pptx")
    assert any("(North pier, South pier)" in w for w in b["warnings"])
    assert o["slides"] == 2 and "Order a boat" in _texts(tmp_path / "o.pptx")
    assert any("never closed" in w for w in o["warnings"])
    assert j["slides"] == 1 and "Six boats" in _texts(tmp_path / "j.pptx")
    assert any("not valid JSON" in w for w in j["warnings"])


def test_a_rebuild_of_the_same_deck_is_built_again_not_refused(tools, tmp_path):
    skill, fns, _r = tools
    md = "## Ferry crossings rose\n- 1,180 a week\n"
    must = ["Pier repair finishes in May"]
    out = str(tmp_path / "ferry.pptx")
    with bind(_Host(), skill):
        passed = fns["build_deck_from_markdown"](markdown=md, output_path=out)
        first = fns["build_deck_from_markdown"](
            markdown=md, output_path=out, required_items=must
        )
        again = fns["build_deck_from_markdown"](
            markdown=md, output_path=out, required_items=must
        )
        fixed = fns["build_deck_from_markdown"](
            markdown=md + "- Pier repair finishes in May\n",
            output_path=out,
            required_items=must,
        )
    assert "Report it to the user now" in passed["next"]
    assert first["status"] == again["status"] == "built_with_issues"
    assert f"update_slide(deck_path='{out}'" in first["next"]
    assert "never call it verified" in first["next"]
    assert again["next"].startswith("Stop building") and out in again["next"]
    assert fixed["status"] == "ok"


# --------------------------------------------------------------------------
# Content findings are warnings on a built deck
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text, seen, markdown, expected",
    [
        (
            _REGIONS,
            [],
            _share_slide("45%"),
            ["in nothing the user said", "45", "update_slide"],
        ),
        (
            "One slide on how the quarter went.",
            [
                ("load_skill", {"name": "slide-decks"}, "Revenue $12.4M; West +37%"),
                ("slide-decks/build_deck_from_markdown", {}, '{"slides": 12.4}'),
            ],
            "## Revenue hit $12.4M\n- A strong quarter\n",
            ["in nothing the user said", "12.4"],
        ),
        (
            "One slide on our office move to Austin in March.",
            [],
            "## The move wraps up in March\n- Jan 15: lease signed\n- Mar 10: go live\n",
            ["in nothing the user said", "10, 15"],
        ),
        (
            "One slide: grow in Mexico, launch the app.",
            [],
            "## Three priorities\n- 🇲🇽 Grow in Mexico\n- 📱 Launch the app\n",
            ["emoji", "📱"],
        ),
        (
            "Two slides on our interviews: setup is slow (9 of 14), calendar "
            "wanted (7 of 14), privacy worries (6 of 14).",
            [],
            "## Setup is the top pain\n- 9 of 14 said setup is slow\n"
            "Notes: also calendar (7 of 14) and privacy (6 of 14)\n"
            "## Next\n- Cut setup time\n",
            ["only in the speaker notes", "6, 7"],
        ),
        (
            "One slide: 9 of 14 said setup is slow and 7 of 14 want calendar sync.",
            [],
            "## Setup is the top pain\n- 9 of 14 say setup is slow\n- 7 want calendar sync\n",
            ["without its base", "7 of 14"],
        ),
    ],
)
def test_a_content_finding_is_a_warning_on_a_built_deck(
    tools, tmp_path, request_text, seen, markdown, expected
):
    skill, fns, _r = tools
    out = tmp_path / "w.pptx"
    with bind(_Seeing(request_text, seen), skill):
        r = fns["build_deck_from_markdown"](markdown=markdown, output_path=str(out))
    assert r["status"] != "error" and out.is_file(), r
    said = [w for w in r["warnings"] if expected[0] in w]
    assert said, r["warnings"]
    for part in expected[1:]:
        assert part in said[0]


# --------------------------------------------------------------------------
# Where a deck is saved, and which pictures it may read
# --------------------------------------------------------------------------


class _Sandboxed(_Asking):
    """An agent confined to *allowed*, whose user answers no to every prompt."""

    def __init__(self, *allowed) -> None:
        from gaia.security import PathValidator

        super().__init__("")
        self.path_validator = PathValidator(allowed_paths=[str(a) for a in allowed])
        self.path_validator.set_access_prompt(lambda path: False)


_PLAN = "# Ferry plan\n\n## Crossings rose after the timetable change\n- 1,180 a week\n"


def test_a_deck_with_no_path_saves_in_the_working_folder(tools, tmp_path):
    skill, fns, _r = tools
    work = tmp_path / "work"
    with bind(_Sandboxed(work), skill):
        unnamed = fns["build_deck_from_markdown"](markdown=_PLAN)
        named = fns["build_deck_from_markdown"](
            markdown=_PLAN, output_path="ferry.pptx"
        )
        null = fns["build_deck_from_markdown"](markdown=_PLAN, output_path=None)
    assert unnamed["status"] == named["status"] == null["status"] == "ok", unnamed
    assert unnamed["path"] == str(work / "Ferry plan.pptx")
    assert named["path"] == str(work / "ferry.pptx")
    assert null["path"] == unnamed["path"]
    assert _count(work / "Ferry plan.pptx") == 2
    assert any(str(work) in n and "full path" in n for n in unnamed["notes"])


def test_the_default_folder_is_one_this_chat_may_write(tools, tmp_path):
    """GAIA's own documents folder is outside a chat's folders: saving there was denied."""
    from gaia.config import gaia_home

    skill, fns, _r = tools
    agent = _Sandboxed(tmp_path / "work")
    old_default = gaia_home() / "documents" / "Ferry plan.pptx"
    allowed, _why = agent.path_validator.validate_write(
        str(old_default), prompt_user=False
    )
    with bind(agent, skill):
        r = fns["build_deck_from_markdown"](markdown=_PLAN)
    assert not allowed
    assert r["status"] == "ok", r
    assert agent.path_validator.validate_write(r["path"], prompt_user=False)[0]
    assert not old_default.exists()


def test_with_the_working_folder_out_of_scope_an_allowed_folder_is_used(
    tools, tmp_path
):
    skill, fns, _r = tools
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    with bind(_Sandboxed(allowed), skill):
        r = fns["build_deck_from_markdown"](markdown=_PLAN)
    assert r["status"] == "ok", r
    assert Path(r["path"]).parent == allowed
    assert not list((tmp_path / "work").glob("*.pptx"))


def test_no_writable_folder_is_an_error_naming_the_next_call(tools, tmp_path):
    skill, fns, _r = tools
    with bind(_Sandboxed(), skill):
        r = fns["build_deck_from_markdown"](markdown=_PLAN)
    assert r["status"] == "error"
    assert "output_path=" in r["problems"][0]
    assert not list(tmp_path.rglob("*.pptx"))


def test_an_output_path_outside_the_allowed_folders_is_refused(tools, tmp_path):
    skill, fns, _r = tools
    outside = tmp_path / "outside"
    outside.mkdir()
    with bind(_Sandboxed(tmp_path / "work"), skill):
        r = fns["build_deck_from_markdown"](
            markdown=_PLAN, output_path=str(outside / "deck.pptx")
        )
    assert r["status"] == "error" and "output_path=''" in r["error"]
    assert not (outside / "deck.pptx").exists()


@pytest.fixture
def pictures(tmp_path):
    """One picture inside the allowed folder and one outside it."""
    from PIL import Image

    inside = tmp_path / "work" / "pier.png"
    outside = tmp_path / "private" / "secret.png"
    outside.parent.mkdir()
    for path in (inside, outside):
        Image.new("RGB", (320, 180), (30, 90, 160)).save(path)
    return inside, outside


def _image_slide(key: str, path) -> dict:
    return {"type": "image", "title": "The new pier", key: str(path)}


@pytest.mark.parametrize("key", ["path", "image", "src", "image_path", "file"])
@pytest.mark.parametrize("as_text", [False, True])
def test_every_spelling_of_an_image_path_is_scope_checked(
    tools, tmp_path, pictures, key, as_text
):
    """image_path, file and a slide sent as JSON text used to skip the read check."""
    skill, fns, _r = tools
    inside, outside = pictures
    work = tmp_path / "work"

    def slide(path):
        spec = _image_slide(key, path)
        return json.dumps(spec) if as_text else spec

    with bind(_Sandboxed(work), skill):
        refused = fns["build_deck"](
            outline={"slides": [slide(outside)]}, output_path=str(work / "no.pptx")
        )
        built = fns["build_deck"](
            outline={"slides": [slide(inside)]}, output_path=str(work / "yes.pptx")
        )
        added = fns["add_slide"](deck_path=str(work / "yes.pptx"), slide=slide(outside))
        replaced = fns["update_slide"](
            deck_path=str(work / "yes.pptx"), index=1, slide=slide(outside)
        )
        wrapped = fns["add_slide"](
            deck_path=str(work / "yes.pptx"), slide={"slides": [slide(outside)]}
        )
    for r in (refused, added, replaced, wrapped):
        assert r["status"] == "error", r
        assert "outside the folders this chat may read" in r["error"]
        assert "Call again without that file" in r["error"]
    assert not (work / "no.pptx").exists()
    assert built["status"] == "ok", built
    assert _count(work / "yes.pptx") == 1
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    shapes = Presentation(str(work / "yes.pptx")).slides[0].shapes
    assert any(s.shape_type == MSO_SHAPE_TYPE.PICTURE for s in shapes)


def test_an_outline_sent_as_json_text_is_scope_checked(tools, tmp_path, pictures):
    skill, fns, _r = tools
    _inside, outside = pictures
    work = tmp_path / "work"
    text = json.dumps({"deck": {"pages": [_image_slide("file", outside)]}})
    md = f"## The new pier\n![The pier]({outside})\n"
    with bind(_Sandboxed(work), skill):
        j = fns["build_deck"](outline=text, output_path=str(work / "j.pptx"))
        m = fns["build_deck_from_markdown"](
            markdown=md, output_path=str(work / "m.pptx")
        )
    for r in (j, m):
        assert r["status"] == "error", r
        assert "outside the folders this chat may read" in r["error"]
    assert not list(work.glob("*.pptx"))


def test_a_picture_that_is_not_there_costs_only_the_picture(tools, tmp_path):
    skill, fns, _r = tools
    work = tmp_path / "work"
    out = work / "p.pptx"
    with bind(_Sandboxed(work), skill):
        r = fns["build_deck"](
            outline={
                "slides": [
                    _image_slide("image_path", work / "gone.png"),
                    _image_slide("file", "relative.png"),
                ]
            },
            output_path=str(out),
        )
    assert r["status"] == "built_with_issues" and _count(out) == 2, r
    missing = [i for i in r["verify"]["issues"] if i["check"] == "image_missing"]
    assert len(missing) == 2
    assert "does not exist" in missing[0]["detail"]
    assert "not an absolute path" in missing[1]["detail"]


def test_every_tool_shows_one_example_call(tools):
    _skill, fns, _r = tools
    for name, fn in fns.items():
        doc = fn.__doc__ or ""
        assert (
            f"Example: {name}(" in doc or f"Example (one slide): {name}(" in doc
        ), name
