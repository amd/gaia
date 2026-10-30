# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The guide-card tools: what a valid call returns, what a bad one is told,
and that the card reaches the wire as ``tool_result.render == "guide"``."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.registry import KNOWN_TOOLS
from gaia.agents.tools.guide_tools import GUIDE_TOOL_NAMES, GuideToolsMixin
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator


class _GuideAgent(Agent, GuideToolsMixin):
    def _get_system_prompt(self) -> str:
        return ""

    def _register_tools(self) -> None:
        self.register_guide_tools()


@pytest.fixture
def tools():
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        with patch("gaia.agents.base.agent.AgentSDK"):
            _GuideAgent(skip_lemonade=True, silent_mode=True)
        yield {name: _TOOL_REGISTRY[name] for name in GUIDE_TOOL_NAMES}
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _call(tools, name, **kwargs):
    return tools[name]["function"](**kwargs)


_RECIPE = {
    "title": "Banana bread",
    "servings": 8,
    "ingredients": ["3 ripe bananas", "2 cups flour"],
    "steps": ["Mash: mash the bananas", "Bake 60 minutes"],
}


# --- valid payloads ---------------------------------------------------------


def test_recipe_card_carries_servings_ingredients_and_steps(tools):
    result = _call(tools, "show_recipe", **_RECIPE, notes="Freezes well.")

    assert result == {
        "ok": True,
        "message": result["message"],
        "data": {
            "kind": "guide",
            "guideType": "recipe",
            "title": "Banana bread",
            "description": "",
            "notes": "Freezes well.",
            "servings": 8,
            "items": ["3 ripe bananas", "2 cups flour"],
            "steps": ["Mash: mash the bananas", "Bake 60 minutes"],
        },
    }


def test_a_shown_card_tells_the_model_not_to_repeat_it(tools):
    message = _call(tools, "show_checklist", title="Beach", items=["Towel"])["message"]

    assert "Do not repeat" in message


def test_workout_card_puts_equipment_in_items_and_exercises_in_steps(tools):
    data = _call(
        tools,
        "show_workout",
        title="Upper body",
        exercises=["Push-ups: 3 sets of 12, rest 45 seconds"],
        equipment=["Mat"],
    )["data"]

    assert data["guideType"] == "workout"
    assert data["items"] == ["Mat"]
    assert data["steps"] == ["Push-ups: 3 sets of 12, rest 45 seconds"]
    assert data["servings"] is None


def test_workout_without_equipment_has_no_items(tools):
    data = _call(tools, "show_workout", title="Run", exercises=["Jog 20 minutes"])[
        "data"
    ]
    assert data["items"] == []


def test_how_to_card_puts_materials_in_items(tools):
    data = _call(
        tools,
        "show_how_to",
        title="Replace a bike tube",
        steps=["Remove the wheel"],
        materials=["Tire levers"],
        description="Ten minutes with the right tools.",
    )["data"]

    assert data["guideType"] == "howto"
    assert data["items"] == ["Tire levers"]
    assert data["steps"] == ["Remove the wheel"]
    assert data["description"] == "Ten minutes with the right tools."
    assert data["servings"] is None


def test_checklist_card_has_items_and_no_steps(tools):
    data = _call(
        tools, "show_checklist", title="Camping", items=["Tent", "Sleeping bag"]
    )["data"]

    assert data["guideType"] == "checklist"
    assert data["items"] == ["Tent", "Sleeping bag"]
    assert data["steps"] == []
    assert data["notes"] == ""


def test_itinerary_card_puts_days_in_steps_and_packing_in_items(tools):
    data = _call(
        tools,
        "show_itinerary",
        title="4 days in Lisbon",
        days=["Day 1 - Lisbon: Alfama walk, tram 28", "Day 2 - Sintra day trip"],
        packing=["Walking shoes"],
        notes="Book Pena Palace ahead.",
    )["data"]

    assert data["guideType"] == "itinerary"
    assert data["steps"] == [
        "Day 1 - Lisbon: Alfama walk, tram 28",
        "Day 2 - Sintra day trip",
    ]
    assert data["items"] == ["Walking shoes"]
    assert data["notes"] == "Book Pena Palace ahead."
    assert data["servings"] is None


def test_itinerary_without_packing_has_no_items(tools):
    data = _call(tools, "show_itinerary", title="Day trip", days=["Day 1 - Porto"])[
        "data"
    ]
    assert data["items"] == []


def test_itinerary_needs_a_day(tools):
    result = _call(tools, "show_itinerary", title="Nowhere", days=[])
    assert result["ok"] is False
    assert "show_itinerary needs at least one entry in days" in result["error"]


def test_itinerary_validates_packing_lines(tools):
    result = _call(
        tools, "show_itinerary", title="T", days=["Day 1 - Rome"], packing=[3]
    )
    assert result["ok"] is False
    assert "packing[0] is int" in result["error"]


def test_lines_are_passed_through_unchanged(tools):
    """Validation only: nothing is trimmed or rewritten."""
    data = _call(tools, "show_checklist", title=" Trip ", items=["  Tent  "])["data"]
    assert data["title"] == " Trip "
    assert data["items"] == ["  Tent  "]


def test_limits_are_inclusive(tools):
    result = _call(
        tools,
        "show_recipe",
        title="t" * 120,
        servings=99,
        ingredients=["x" * 500] * 100,
        steps=["s"] * 60,
    )
    assert result["ok"] is True
    assert _call(tools, "show_recipe", **{**_RECIPE, "servings": 1})["ok"] is True


# --- validation errors ------------------------------------------------------


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"title": ""}, "title of 1 to 120 characters"),
        ({"title": "   "}, "title of 1 to 120 characters"),
        ({"title": "t" * 121}, "got 121 characters"),
        ({"title": None}, "got NoneType"),
        ({"servings": 0}, "servings as a whole number from 1 to 99"),
        ({"servings": 100}, "got 100"),
        ({"servings": "4"}, "got '4'"),
        ({"servings": True}, "got True"),
        ({"ingredients": "2 eggs, flour"}, "ingredients must be a list"),
        ({"ingredients": ["2 eggs", ""]}, "ingredients[1] is empty"),
        ({"ingredients": ["2 eggs", 3]}, "ingredients[1] is int"),
        ({"ingredients": ["x" * 501]}, "ingredients[0] is 501 characters"),
        (
            {"ingredients": ["x"] * 101},
            "ingredients has 101 lines and the limit is 100",
        ),
        ({"steps": ["s"] * 61}, "steps has 61 lines and the limit is 60"),
        ({"steps": []}, "needs at least one entry in steps"),
        ({"notes": None}, "notes must be text"),
        ({"description": ["intro"]}, "description must be text"),
    ],
)
def test_recipe_errors_name_what_failed(tools, overrides, expected):
    result = _call(tools, "show_recipe", **{**_RECIPE, **overrides})

    assert result["ok"] is False
    assert expected in result["error"]
    assert result["error"].startswith("show_recipe")
    assert "data" not in result


def test_workout_needs_an_exercise(tools):
    result = _call(tools, "show_workout", title="Rest day", exercises=[])
    assert result == {
        "ok": False,
        "error": (
            "show_workout needs at least one entry in exercises. "
            "Send one line per step."
        ),
    }


def test_workout_validates_equipment_lines(tools):
    result = _call(
        tools, "show_workout", title="W", exercises=["Squats"], equipment=[""]
    )
    assert result["ok"] is False
    assert "equipment[0] is empty" in result["error"]


def test_how_to_needs_a_step(tools):
    result = _call(tools, "show_how_to", title="Fix it", steps=[])
    assert result["ok"] is False
    assert "show_how_to needs at least one entry in steps" in result["error"]


def test_checklist_needs_an_item(tools):
    result = _call(tools, "show_checklist", title="Empty", items=[])
    assert result["ok"] is False
    assert "show_checklist needs at least one entry in items" in result["error"]


def test_a_failed_card_reads_as_a_failed_tool_call(tools):
    from gaia.agents.base.tool_outcome import tool_result_failed

    assert tool_result_failed(_call(tools, "show_checklist", title="", items=["x"]))
    assert not tool_result_failed(_call(tools, "show_recipe", **_RECIPE))


# --- registration -----------------------------------------------------------


def test_registered_as_a_composable_mixin():
    assert KNOWN_TOOLS["guide"] == ("gaia.agents.tools.guide_tools", "GuideToolsMixin")


def test_each_tool_ships_its_trigger_words_and_reply_rule(tools):
    triggers = {
        "show_recipe": "recipe",
        "show_workout": "workout",
        "show_how_to": "how to fix",
        "show_checklist": "shopping list",
        "show_itinerary": "itinerary",
    }
    for name, word in triggers.items():
        description = tools[name]["description"]
        assert word in description.splitlines()[0], name
        assert "Do not repeat the list or steps." in description, name
        assert "Args:" not in description, name


def test_list_arguments_are_advertised_as_string_arrays(tools):
    params = tools["show_recipe"]["parameters"]
    assert params["ingredients"]["type"] == "array"
    assert params["ingredients"]["items"] == {"type": "string"}
    assert params["servings"]["type"] == "integer"
    assert params["description"]["required"] is False
    assert tools["show_workout"]["parameters"]["equipment"]["required"] is False


# --- the card reaches the wire ---------------------------------------------


@pytest.mark.parametrize("tool_name", GUIDE_TOOL_NAMES)
def test_every_guide_tool_renders_as_a_guide_card(tool_name):
    assert SSEOutputHandler._RENDER_TOOL_TO_LANG[tool_name] == "guide"


def _drain(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


@pytest.mark.parametrize("as_json", [False, True])
def test_handler_lifts_the_card_and_translator_marks_it_guide(tools, as_json):
    envelope = _call(tools, "show_recipe", **_RECIPE)
    handler = SSEOutputHandler()
    translator = CanonicalTranslator("r1", agent_id="gaia")

    handler.print_tool_usage("show_recipe")
    handler.pretty_print_json(dict(_RECIPE), title="Arguments")
    handler.pretty_print_json(
        json.dumps(envelope) if as_json else envelope, title="Result"
    )

    wire = [out for event in _drain(handler) for out in translator.translate(event)]
    (result,) = [e for e in wire if e["type"] == "tool_result"]
    assert result["tool"] == "show_recipe"
    assert result["render"] == "guide"
    assert result["data"] == envelope["data"]


def test_a_failed_card_reaches_the_wire_without_a_card(tools):
    envelope = _call(tools, "show_checklist", title="", items=["x"])
    handler = SSEOutputHandler()
    handler.print_tool_usage("show_checklist")
    _drain(handler)
    handler.pretty_print_json(envelope, title="Result")

    (event,) = _drain(handler)
    assert event["type"] == "tool_result"
    assert event["success"] is False
    assert "result_data" not in event
