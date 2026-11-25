# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""How the SDK speaks the wire, against a stand-in engine. No binary needed."""

import json
from pathlib import Path
from typing import List, Literal, Optional

import pytest

from gaia_sdk import (
    PROTOCOL,
    Agent,
    Budget,
    EngineError,
    EngineNotFound,
    Prices,
    ToolRefusal,
    tool,
)

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "fixtures"
        / "stdio"
        / "gaia_host_wire.json"
    ).read_text(encoding="utf-8")
)


@tool
def get_order(order_number: str) -> dict:
    """Look up an order by its number.

    Args:
        order_number: The number printed on the receipt.
    """
    return {"status": "shipped", "order": order_number}


@tool
def refuse_order(order_number: str) -> dict:
    """Look up an order that is never confirmed."""
    raise ToolRefusal("That order has not been confirmed.", order=order_number)


@tool
def broken_order(order_number: str) -> dict:
    """Look up an order in an archive that is down."""
    raise RuntimeError("archive offline")


def _agent(fake_engine, scenario="answer", **kwargs):
    kwargs.setdefault("base_url", "http://127.0.0.1:9/v1")
    return Agent(model=scenario, engine=fake_engine, **kwargs)


def test_sdk_speaks_the_fixtures_protocol_version():
    assert PROTOCOL == FIXTURE["protocol"]


def test_init_and_turn_carry_only_fields_the_wire_allows(fake_engine):
    with _agent(
        fake_engine,
        "echo-init",
        api_key_env="PROVIDER_KEY",
        system_prompt="You answer questions.",
        tools=[get_order],
        max_steps=8,
        max_output_tokens=1200,
        context_size=64000,
        temperature=0.1,
        budget=Budget(model_calls=8, output_tokens=3600, seconds=25),
    ) as agent:
        sent = json.loads(agent.run("hi").text)

    init_spec = FIXTURE["host_to_engine"]["init"]
    assert set(init_spec["required"]) <= set(sent["init"])
    assert set(sent["init"]) <= set(init_spec["required"]) | set(init_spec["optional"])
    turn_spec = FIXTURE["host_to_engine"]["turn"]
    assert set(sent["turn"]) == set(turn_spec["required"]) | set(turn_spec["optional"])
    assert set(sent["turn"]["budget"]) == set(FIXTURE["budget"]) - {"usd"}
    tool_spec = FIXTURE["tool"]
    declared = sent["init"]["tools"][0]
    assert set(declared) == {"name", "description", "parameters"}
    assert set(declared) <= set(tool_spec["required"]) | set(tool_spec["optional"])
    assert sent["init"]["api_key_env"] == "PROVIDER_KEY"
    assert sent["turn"]["messages"] == [{"role": "user", "content": "hi"}]


def test_tool_schema_comes_from_hints_and_docstring():
    assert get_order.declaration() == {
        "name": "get_order",
        "description": "Look up an order by its number.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_number": {
                    "type": "string",
                    "description": "The number printed on the receipt.",
                }
            },
            "required": ["order_number"],
        },
    }


def test_optional_list_and_literal_arguments():
    @tool
    def search(
        query: str,
        tags: List[str],
        sort: Literal["new", "old"] = "new",
        limit: Optional[int] = None,
    ) -> dict:
        """Search orders."""
        return {}

    schema = search.parameters
    assert schema["required"] == ["query", "tags"]
    assert schema["properties"]["tags"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert schema["properties"]["sort"] == {"type": "string", "enum": ["new", "old"]}
    assert schema["properties"]["limit"] == {"type": "integer"}


@pytest.mark.parametrize(
    "source, match",
    [
        ("def f(x):\n    'Doc.'\n", "no type hint"),
        ("def f(x: str):\n    pass\n", "no description"),
        ("def f(*args: str):\n    'Doc.'\n", "args or \\*\\*kwargs"),
        ("def f(x: set):\n    'Doc.'\n", "no JSON form"),
    ],
)
def test_function_the_model_cannot_be_told_about_is_refused(source, match):
    scope = {}
    exec(source, scope)  # noqa: S102 - building small functions for the test
    with pytest.raises(TypeError, match=match):
        tool(scope["f"])


def test_tool_results_refusals_and_failures_go_back_as_the_wire_says(fake_engine):
    with _agent(
        fake_engine, "call-each-tool", tools=[get_order, refuse_order, broken_order]
    ) as agent:
        turn = agent.run("where is it?")

    ok, refused, broken = turn.tool_calls
    assert ok == {
        "name": "get_order",
        "arguments": {"order_number": "1001"},
        "result": {"status": "shipped", "order": "1001"},
        "failed": False,
    }
    assert refused["failed"] is False
    assert refused["result"] == {
        "status": "refused",
        "message": "That order has not been confirmed.",
        "order": "1001",
    }
    assert broken["failed"] is True
    assert broken["result"] == "RuntimeError: archive offline"


def test_request_for_a_tool_that_is_not_registered_is_answered_with_an_error(
    fake_engine,
):
    with _agent(fake_engine, "unknown-tool", tools=[get_order]) as agent:
        answer = json.loads(agent.run("hi").text)

    assert answer["type"] == "tool_result"
    assert "no_such_tool" in answer["error"]
    assert "result" not in answer


def test_turn_reports_each_model_call(fake_engine):
    seen = []
    with _agent(fake_engine, on_model_call=seen.append) as agent:
        turn = agent.run("hi")

    assert turn.ok and turn.text == "Hello there."
    assert turn.model_calls == seen
    assert seen[0]["usage"]["output_tokens"] == 7
    assert set(seen[0]) == {"step", "seconds", "usage"}


def test_turn_ids_count_up(fake_engine):
    with _agent(fake_engine, "echo-init") as agent:
        first = json.loads(agent.run("one").text)["turn"]["id"]
        second = json.loads(agent.run("two").text)["turn"]["id"]

    assert (first, second) == ("t1", "t2")


def test_refused_init_raises_with_the_engines_code(fake_engine):
    with pytest.raises(EngineError, match="refused on purpose") as caught:
        _agent(fake_engine, "refuse-init")

    assert caught.value.code == "bad_init"


def test_engine_on_another_protocol_is_refused(fake_engine):
    with pytest.raises(EngineError, match="speaks protocol 1"):
        _agent(fake_engine, "old-protocol")


def test_engine_that_exits_at_start_is_reported(fake_engine):
    with pytest.raises(EngineError, match="exit code 3"):
        _agent(fake_engine, "die-at-init")


def test_refused_turn_raises_and_the_agent_can_be_closed(fake_engine):
    agent = _agent(fake_engine, "bad-turn")

    with pytest.raises(EngineError) as caught:
        agent.run("hi")

    assert caught.value.code == "bad_turn"
    agent.close()


def test_engine_that_dies_mid_turn_is_reported_not_hung(fake_engine):
    with _agent(fake_engine, "die-mid-turn") as agent:
        with pytest.raises(EngineError, match="exit code 9"):
            agent.run("hi")


def test_event_from_a_newer_engine_is_named(fake_engine):
    with _agent(fake_engine, "new-event") as agent:
        with pytest.raises(EngineError, match="from_the_future"):
            agent.run("hi")


def test_missing_engine_says_how_to_get_one(monkeypatch, tmp_path):
    monkeypatch.delenv("GAIA_ENGINE", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(EngineNotFound, match="GAIA_ENGINE"):
        Agent(model="m", base_url="http://127.0.0.1:9/v1")


def test_engine_path_that_is_not_a_file_is_named(tmp_path):
    with pytest.raises(EngineNotFound, match="is not a file"):
        Agent(model="m", base_url="http://x/v1", engine=str(tmp_path / "missing"))


def test_two_tools_with_one_name_are_refused(fake_engine):
    with pytest.raises(ValueError, match="Two tools are named"):
        _agent(fake_engine, tools=[get_order, get_order])


@pytest.mark.parametrize(
    "kwargs", [{"model_calls": 0}, {"output_tokens": True}, {"seconds": -1}]
)
def test_budget_rejects_limits_that_are_not_positive(kwargs):
    with pytest.raises(ValueError, match="Budget"):
        Budget(**kwargs)


def test_tool_limits_and_prices_go_out_under_the_wires_names(fake_engine):
    @tool(max_calls_per_turn=2, terminal=True)
    def handoff(summary: str) -> dict:
        """Pass the conversation to a person."""
        return {}

    with _agent(
        fake_engine,
        "echo-init",
        tools=[handoff, get_order],
        prices=Prices(input=0.3, cached_input=0.006, output=1.2),
        budget=Budget(usd=0.03),
    ) as agent:
        sent = json.loads(agent.run("hi").text)

    limited, plain = sent["init"]["tools"]
    allowed = set(FIXTURE["tool"]["required"]) | set(FIXTURE["tool"]["optional"])
    assert set(limited) <= allowed
    assert (limited["max_calls_per_turn"], limited["terminal"]) == (2, True)
    assert "max_calls_per_turn" not in plain and "terminal" not in plain
    assert set(sent["init"]["prices"]) == set(FIXTURE["prices"])
    assert sent["turn"]["budget"] == {"usd": 0.03}
    assert set(sent["turn"]["budget"]) <= set(FIXTURE["budget"])


def test_dollar_budget_without_prices_is_refused_before_anything_is_sent(fake_engine):
    with pytest.raises(ValueError, match="needs the agent's prices"):
        _agent(fake_engine, budget=Budget(usd=0.03))

    with _agent(fake_engine) as agent:
        with pytest.raises(ValueError, match="needs the agent's prices"):
            agent.run("hi", budget=Budget(usd=0.03))


@pytest.mark.parametrize(
    "kwargs", [{"max_calls_per_turn": 0}, {"max_calls_per_turn": True}, {"terminal": 1}]
)
def test_bad_tool_limits_are_refused(kwargs):
    with pytest.raises(TypeError):
        tool(**kwargs)


def test_prices_must_not_be_negative():
    with pytest.raises(ValueError, match="Prices.output"):
        Prices(input=1.0, cached_input=0.0, output=-1.0)
