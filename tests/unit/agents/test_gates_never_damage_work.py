# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Answer gates ask or caveat; they never misread the turn or damage its work.

Two false positives, both seen in real runs:

- Look-first told a turn that had run every tool call as a plan step that it
  "answered without opening anything": plan steps never reached the log the
  gate read.
- The file-figure check sent the model back to "fix the file, or mark them in
  it as estimates" over a figure it had computed correctly, and parsed
  "$293.5B" as bytes, so a correct deliverable could be edited into a wrong one.
"""

from __future__ import annotations

import json

import pytest

from gaia.agents.base.deliverables import FILE, file_findings
from gaia.agents.base.grounding import content_findings
from gaia.agents.base.verification import (
    VERIFICATION_NOTE_OPENER,
    strip_verification_scope,
)
from tests.unit.agents import test_deliverable_grounding as deliverable_tests
from tests.unit.agents import test_look_first as look_tests

_call = deliverable_tests._call
_fetch = deliverable_tests._fetch
_md = deliverable_tests._md
_read = deliverable_tests._read
_stub_chat = deliverable_tests._stub_chat
_answer = look_tests._answer
_read_call = look_tests._read
_script = look_tests._script
# Fixtures, shared with the modules that own them.
bom_agent = deliverable_tests.bom_agent
agent = look_tests.agent
workspace = look_tests.workspace

LOOK_FIRST = "without opening anything"
REVIEW = "It assigns x = 1 and nothing else."


def _plan(*paths):
    return json.dumps(
        {
            "thought": "",
            "plan": [
                {"tool": "look_first_test_reader", "tool_args": {"file_path": p}}
                for p in paths
            ],
        }
    )


def _user_messages(sent):
    return [str(m.get("content")) for m in sent[-1] if m.get("role") == "user"]


# ---------------------------------------------------------------------------
# Look-first sees tool calls that ran as plan steps
# ---------------------------------------------------------------------------


def test_a_turn_whose_calls_all_ran_as_plan_steps_is_not_told_to_look(agent, workspace):
    sent = _script(
        agent, _plan("toybox/dates.py", "README.md"), _answer(REVIEW), _answer(REVIEW)
    )

    result = agent.process_query("Review toybox/dates.py", max_steps=10)

    assert not any(LOOK_FIRST in m for m in _user_messages(sent))
    assert REVIEW in result["result"]


def test_a_genuine_no_tool_answer_is_still_sent_back_once(agent, workspace):
    guess = "The file is missing, so I can't review it."
    sent = _script(agent, _answer(guess), _answer(guess))

    agent.process_query("Review toybox/dates.py", max_steps=10)

    assert sum(LOOK_FIRST in m for m in _user_messages(sent)) == 1


def test_plan_steps_and_direct_calls_together_are_not_told_to_look(agent, workspace):
    sent = _script(
        agent,
        _plan("README.md"),
        _read_call("toybox/dates.py"),
        _answer(REVIEW),
        _answer(REVIEW),
    )

    result = agent.process_query("Review toybox/dates.py", max_steps=10)

    assert not any(LOOK_FIRST in m for m in _user_messages(sent))
    assert REVIEW in result["result"]


def test_planning_text_after_plan_steps_is_not_told_it_called_no_tools(
    agent, workspace
):
    sent = _script(
        agent,
        _plan("toybox/dates.py"),
        _answer("Let me check the file next."),
        _answer(REVIEW),
        _answer(REVIEW),
    )

    agent.process_query("Review toybox/dates.py", max_steps=10)

    users = _user_messages(sent)
    assert not any("called no tools" in m for m in users)
    assert not any(LOOK_FIRST in m for m in users)


# ---------------------------------------------------------------------------
# The file check caveats; it never sends the model back to edit the file
# ---------------------------------------------------------------------------

#: $95.00 is on the fetched page; the 3-year cost of ownership is the model's
#: own correct arithmetic over more than one step, so no single source holds it.
COMPUTED_BOM = (
    "| Item | Qty | Unit price | Line total |\n|---|---|---|---|\n"
    "| CM5108032 | 40 | $95.00 | $3,800.00 |\n\n"
    "3-year cost of ownership at 7% upkeep a year: $4,598.00\n"
)
ANSWER = "Wrote `bom.md`: 40 modules at $95.00, $3,800.00 up front."

#: Every sentence the old file re-ask put in front of the model.
CORRECTION_TEXT = (
    "[check:",
    "fix the file",
    "mark them in it",
    "reading the file back",
    "one short line",
    "don't explain this check",
    "Keep your answer about the work",
)


def test_a_correct_deliverable_with_a_computed_total_gets_a_caveat_not_a_reask(
    bom_agent,
):
    sent = _stub_chat(
        bom_agent,
        _call("fetch_page", url="https://example.com/cm5"),
        _call("write_file", file_path="bom.md", content=COMPUTED_BOM),
        _answer(ANSWER),
    )

    result = bom_agent.process_query("Write bom.md for 40 CM5s.", max_steps=10)

    assert len(sent) == 3, "no extra model call: the check never re-asks"
    with open("bom.md", encoding="utf-8") as handle:
        assert handle.read() == COMPUTED_BOM
    assert strip_verification_scope(result["result"]).strip() == ANSWER
    assert result["result"].count(VERIFICATION_NOTE_OPENER) == 1
    assert "$4,598.00" in result["result"]
    assert "$4,598.00" in " ".join(result["verification"]["ungrounded"])


def test_an_invented_figure_is_still_caveated(bom_agent):
    invented = COMPUTED_BOM + "Spare PSU: $14.99 each\n"
    _stub_chat(
        bom_agent,
        _call("fetch_page", url="https://example.com/cm5"),
        _call("write_file", file_path="bom.md", content=invented),
        _answer(ANSWER),
    )

    result = bom_agent.process_query("Write bom.md for 40 CM5s.", max_steps=10)

    assert "$14.99" in result["result"]
    assert VERIFICATION_NOTE_OPENER in result["result"]


def test_no_check_text_reaches_the_answer_or_the_model(bom_agent):
    sent = _stub_chat(
        bom_agent,
        _call("fetch_page", url="https://example.com/cm5"),
        _call("write_file", file_path="bom.md", content=COMPUTED_BOM),
        _answer(ANSWER),
    )

    result = bom_agent.process_query("Write bom.md for 40 CM5s.", max_steps=10)

    model_saw = " ".join(str(m.get("content")) for m in sent[-1])
    for text in CORRECTION_TEXT:
        assert text not in result["result"], text
        assert text not in model_saw, text


# ---------------------------------------------------------------------------
# A scaled amount is the same figure in every spelling, both ways
# ---------------------------------------------------------------------------

BILLIONS = [
    "$293.5B",
    "$293.5b",
    "$293.5 billion",
    "$293.5bn",
    "US$293.5 B",
    "293.5 billion dollars",
    "$293,500,000,000",
    "293,500,000,000",
]
MILLIONS = ["$14.5M", "$14.5m", "$14.5 million", "14.5 million", "$14,500,000"]
MILLIONS_BARE = MILLIONS + ["14,500,000"]
#: A long prose line, the figure followed by a comma: neither price-tagged nor
#: tabular, which is where a bare "293,500,000,000," used to go unread.
PADDING = (
    ", up from the prior year, per the annual report filed with the regulator, "
    "which also notes the services segment grew while hardware declined, and "
    "more prose so the line runs past two hundred characters."
)


def _pairs(forms):
    return [(a, b) for a in forms for b in forms if a != b]


def _sources(figure):
    return [f"Revenue was {figure} in the year.", f"Revenue of {figure}{PADDING}"]


@pytest.mark.parametrize("written,source", _pairs(BILLIONS) + _pairs(MILLIONS_BARE))
def test_a_file_figure_matches_its_source_in_any_scale(written, source):
    for text in _sources(source):
        findings = file_findings(
            {"memo.md": _md(f"Revenue: {written}.\n")},
            [_read("notes.txt", text)],
            query="write memo.md",
        )
        assert findings == [], (written, text)


@pytest.mark.parametrize("written,source", _pairs(BILLIONS) + _pairs(MILLIONS))
def test_an_answer_figure_matches_its_source_in_any_scale(written, source):
    answer = (
        f"| Item | Count |\n|---|---|\n| Units | 1,234 |\n\nRevenue was {written}.\n"
    )
    for text in _sources(source):
        records = [_read("notes.txt", text), _read("units.txt", "Units 1,234")]
        assert content_findings(answer, records) == [], (written, text)


@pytest.mark.parametrize("written", ["$293.6B", "$29.35B", "$2.935B", "$295.5B"])
def test_a_different_scaled_figure_is_still_flagged(written):
    records = [_read("notes.txt", "Revenue was $293.5 billion in the year.")]

    findings = file_findings(
        {"memo.md": _md(f"Revenue: {written}.\n")}, records, query="write memo.md"
    )

    assert [f.gate for f in findings] == [FILE], written
    answer = (
        f"| Item | Count |\n|---|---|\n| Units | 1,234 |\n\nRevenue was {written}.\n"
    )
    records.append(_read("units.txt", "Units 1,234"))
    assert content_findings(answer, records), written


def test_a_small_bare_number_never_sources_a_price():
    """The bare-number match is for amounts in the millions: "60 Hz" is not $60."""
    findings = file_findings(
        {"bom.md": _md("PSU: $60.00\n")},
        [_fetch("u", "Refresh 60 Hz, 60 frames per second, model 60")],
        query="write bom.md",
    )

    assert findings
