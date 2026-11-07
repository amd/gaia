# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The calculator-card tools: what a valid call returns, what a bad one is
told, and that the card reaches the wire as ``tool_result.render ==
"calculator"``."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.registry import KNOWN_TOOLS
from gaia.agents.tools.calculator_tools import (
    CALCULATOR_TOOL_NAMES,
    CalculatorToolsMixin,
)
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator


class _CalculatorAgent(Agent, CalculatorToolsMixin):
    def _get_system_prompt(self) -> str:
        return ""

    def _register_tools(self) -> None:
        self.register_calculator_tools()


@pytest.fixture
def tools():
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        with patch("gaia.agents.base.agent.AgentSDK"):
            _CalculatorAgent(skip_lemonade=True, silent_mode=True)
        yield {name: _TOOL_REGISTRY[name] for name in CALCULATOR_TOOL_NAMES}
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _call(tools, name, **kwargs):
    return tools[name]["function"](**kwargs)


_LOAN = {"price": 250000, "annual_rate_percent": 6, "years": 30}


# --- valid payloads ---------------------------------------------------------


def test_tip_card_carries_the_starting_numbers(tools):
    result = _call(tools, "show_tip_calculator", bill=84.5, tip_percent=18, people=3)

    assert result == {
        "ok": True,
        "message": result["message"],
        "data": {
            "kind": "calculator",
            "calculator": "tip",
            "title": "",
            "currency": "USD",
            "inputs": {"bill": 84.5, "tipPercent": 18, "people": 3},
        },
    }


def test_tip_defaults_to_18_percent_for_one_person(tools):
    inputs = _call(tools, "show_tip_calculator", bill=40)["data"]["inputs"]
    assert inputs == {"bill": 40, "tipPercent": 18, "people": 1}


def test_a_whole_float_head_count_becomes_an_int(tools):
    inputs = _call(tools, "show_tip_calculator", bill=40, people=4.0)["data"]["inputs"]
    assert inputs["people"] == 4
    assert isinstance(inputs["people"], int)


def test_loan_card_carries_the_starting_numbers(tools):
    data = _call(
        tools, "show_loan_calculator", **_LOAN, down_payment=50000, title="Elm St"
    )["data"]

    assert data == {
        "kind": "calculator",
        "calculator": "loan",
        "title": "Elm St",
        "currency": "USD",
        "inputs": {
            "amount": 250000,
            "annualRatePercent": 6,
            "years": 30,
            "downPayment": 50000,
        },
    }


def test_shown_card_tells_the_model_not_to_do_the_math(tools):
    message = _call(tools, "show_loan_calculator", **_LOAN)["message"]
    assert "Do not do the math" in message


def test_limits_are_inclusive_where_the_range_is_closed(tools):
    assert _call(tools, "show_tip_calculator", bill=1, tip_percent=0)["ok"]
    assert _call(tools, "show_tip_calculator", bill=1, tip_percent=100, people=50)["ok"]
    assert _call(
        tools, "show_loan_calculator", price=1, annual_rate_percent=0, years=50
    )["ok"]
    assert _call(tools, "show_loan_calculator", **{**_LOAN, "annual_rate_percent": 50})[
        "ok"
    ]


# --- validation errors ------------------------------------------------------


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"bill": 0}, "bill as a number above 0"),
        ({"bill": -5}, "got -5"),
        ({"bill": "84.50"}, "got '84.50'"),
        ({"bill": True}, "got True"),
        ({"bill": float("nan")}, "got nan"),
        ({"tip_percent": -1}, "tip_percent as a number from 0 to 100"),
        ({"tip_percent": 101}, "got 101"),
        ({"people": 0}, "people as a whole number from 1 to 50"),
        ({"people": 51}, "got 51"),
        ({"people": 2.5}, "got 2.5"),
        ({"title": None}, "title must be text"),
        ({"title": "t" * 121}, "got 121 characters"),
    ],
)
def test_tip_errors_name_what_failed(tools, overrides, expected):
    result = _call(tools, "show_tip_calculator", **{"bill": 84.5, **overrides})

    assert result["ok"] is False
    assert expected in result["error"]
    assert result["error"].startswith("show_tip_calculator")
    assert "data" not in result


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"price": 0}, "price as a number above 0"),
        ({"annual_rate_percent": -0.5}, "annual_rate_percent as a number from 0 to 50"),
        ({"annual_rate_percent": 51}, "got 51"),
        ({"years": 0}, "years as a number from 1 to 50"),
        ({"years": 0.5}, "years as a number from 1 to 50"),
        ({"years": 51}, "got 51"),
        ({"down_payment": -1}, "down_payment as a number from 0"),
        ({"down_payment": 250000}, "down_payment less than price (250000)"),
    ],
)
def test_loan_errors_name_what_failed(tools, overrides, expected):
    result = _call(tools, "show_loan_calculator", **{**_LOAN, **overrides})

    assert result["ok"] is False
    assert expected in result["error"]
    assert result["error"].startswith("show_loan_calculator")


def test_a_failed_card_reads_as_a_failed_tool_call(tools):
    from gaia.agents.base.tool_outcome import tool_result_failed

    assert tool_result_failed(_call(tools, "show_tip_calculator", bill=0))
    assert not tool_result_failed(_call(tools, "show_loan_calculator", **_LOAN))


# --- registration -----------------------------------------------------------


def test_registered_as_a_composable_mixin():
    assert KNOWN_TOOLS["calculator"] == (
        "gaia.agents.tools.calculator_tools",
        "CalculatorToolsMixin",
    )


def test_each_tool_ships_its_trigger_words_and_reply_rule(tools):
    triggers = {
        "show_tip_calculator": ("tip", "split the bill"),
        "show_loan_calculator": ("mortgage", "car loan", "monthly payment"),
    }
    for name, words in triggers.items():
        description = tools[name]["description"]
        for word in words:
            assert word in description.splitlines()[0], (name, word)
        assert "Do not do the math in text." in description, name
        assert "Args:" not in description, name


def test_numbers_are_advertised_with_their_types(tools):
    tip = tools["show_tip_calculator"]["parameters"]
    assert tip["bill"]["type"] == "number"
    assert tip["people"]["type"] == "integer"
    assert tip["tip_percent"]["required"] is False
    loan = tools["show_loan_calculator"]["parameters"]
    assert loan["years"]["required"] is True
    assert loan["down_payment"]["required"] is False


# --- the card reaches the wire ---------------------------------------------


@pytest.mark.parametrize("tool_name", CALCULATOR_TOOL_NAMES)
def test_every_calculator_tool_renders_as_a_calculator_card(tool_name):
    assert SSEOutputHandler._RENDER_TOOL_TO_LANG[tool_name] == "calculator"


def _drain(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


@pytest.mark.parametrize("as_json", [False, True])
def test_handler_lifts_the_card_and_translator_marks_it_calculator(tools, as_json):
    envelope = _call(tools, "show_loan_calculator", **_LOAN)
    handler = SSEOutputHandler()
    translator = CanonicalTranslator("r1", agent_id="gaia")

    handler.print_tool_usage("show_loan_calculator")
    handler.pretty_print_json(dict(_LOAN), title="Arguments")
    handler.pretty_print_json(
        json.dumps(envelope) if as_json else envelope, title="Result"
    )

    wire = [out for event in _drain(handler) for out in translator.translate(event)]
    (result,) = [e for e in wire if e["type"] == "tool_result"]
    assert result["tool"] == "show_loan_calculator"
    assert result["render"] == "calculator"
    assert result["data"] == envelope["data"]


def test_a_failed_card_reaches_the_wire_without_a_card(tools):
    envelope = _call(tools, "show_tip_calculator", bill=0)
    handler = SSEOutputHandler()
    handler.print_tool_usage("show_tip_calculator")
    _drain(handler)
    handler.pretty_print_json(envelope, title="Result")

    (event,) = _drain(handler)
    assert event["type"] == "tool_result"
    assert event["success"] is False
    assert "result_data" not in event


def test_the_card_carries_the_currency_the_user_named(tools):
    data = _call(
        tools,
        "show_loan_calculator",
        price=300000,
        annual_rate_percent=4,
        years=25,
        currency="eur",
    )["data"]

    assert data["currency"] == "EUR"


@pytest.mark.parametrize("currency", ["euro", "", None, "US"])
def test_a_currency_that_is_not_an_iso_code_names_the_fix(tools, currency):
    result = _call(tools, "show_tip_calculator", bill=40, currency=currency)

    assert result["ok"] is False
    assert "3-letter ISO code" in result["error"]
