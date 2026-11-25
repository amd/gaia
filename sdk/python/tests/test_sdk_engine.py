# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The SDK against the real ``gaiacpp --engine`` and a scripted model server.

Skipped unless a built engine is given in GAIA_ENGINE or is on PATH. These are
the tests that prove the two halves agree; the stand-in engine cannot.
"""

import json

import pytest

from gaia_sdk import Agent, Budget, Prices, ToolRefusal, tool

PROMPT = "You answer questions about orders."


def _tools(ran):
    @tool
    def get_order(order_number: str) -> dict:
        """Look up an order by its number."""
        ran.append(order_number)
        if order_number == "2002":
            raise ToolRefusal("That order has not been confirmed in this chat.")
        return {"status": "shipped", "carrier": "UPS"}

    return [get_order]


def _agent(real_engine, model, ran=None, **kwargs):
    return Agent(
        model="host-model",
        base_url=model.base_url,
        system_prompt=PROMPT,
        tools=_tools(ran if ran is not None else []),
        engine=real_engine,
        **kwargs,
    )


def test_answer_in_one_model_call(real_engine, model):
    model.say("Hello there.")

    with _agent(real_engine, model) as agent:
        turn = agent.run("hi")

    assert turn.ok
    assert (turn.status, turn.stop_reason) == ("success", "answered")
    assert turn.text == "Hello there."
    assert turn.usage == {
        "model_calls": 1,
        "input_tokens": 100,
        "cached_tokens": 0,
        "output_tokens": 7,
    }
    assert len(turn.model_calls) == 1
    assert turn.failure is None


def test_model_gets_the_prompt_the_tools_and_the_key_name_only(
    real_engine, model, monkeypatch
):
    monkeypatch.setenv("SDK_TEST_PROVIDER_KEY", "sk-test-123")
    model.say("Hello there.")

    with _agent(real_engine, model, api_key_env="SDK_TEST_PROVIDER_KEY") as agent:
        agent.run("hi")

    request = model.requests[0]
    assert request["model"] == "host-model"
    assert request["messages"][0]["role"] == "system"
    assert request["messages"][0]["content"].strip() == PROMPT
    assert request["messages"][-1] == {"role": "user", "content": "hi"}
    names = [t["function"]["name"] for t in request["tools"]]
    assert names[0] == "get_order"
    assert set(names) <= {"get_order", "read_tool_output"}
    assert request["tools"][0]["function"]["parameters"]["required"] == ["order_number"]
    assert model.authorization[0] == "Bearer sk-test-123"


def test_tool_runs_in_the_host_and_its_result_reaches_the_model(real_engine, model):
    ran = []
    model.call("get_order", order_number="1001")
    model.say("It has shipped with UPS.")

    with _agent(real_engine, model, ran) as agent:
        turn = agent.run("Where is order 1001?")

    assert ran == ["1001"]
    assert turn.text == "It has shipped with UPS."
    assert turn.tool_calls == [
        {
            "name": "get_order",
            "arguments": {"order_number": "1001"},
            "result": {"status": "shipped", "carrier": "UPS"},
            "failed": False,
        }
    ]
    assert turn.usage["model_calls"] == 2
    assert "UPS" in json.dumps(model.requests[1]["messages"])


def test_refusal_is_an_answer_for_the_model_not_a_failed_call(real_engine, model):
    model.call("get_order", order_number="2002")
    model.say("Please confirm that order first.")

    with _agent(real_engine, model) as agent:
        turn = agent.run("Where is order 2002?")

    assert turn.ok
    assert turn.tool_calls[0]["failed"] is False
    assert turn.tool_calls[0]["result"]["status"] == "refused"
    assert "not been confirmed" in json.dumps(model.requests[1]["messages"])


def test_conversation_so_far_is_what_the_model_sees(real_engine, model):
    model.say("On Monday.")

    with _agent(real_engine, model) as agent:
        agent.run(
            [
                {"role": "user", "content": "Where is order 1001?"},
                {"role": "assistant", "content": "It has shipped."},
                {"role": "user", "content": "and when?"},
            ]
        )

    roles = [m["role"] for m in model.requests[0]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_model_call_budget_stops_the_turn_and_the_tool_is_not_run(real_engine, model):
    ran = []
    model.call("get_order", order_number="1001")
    model.say("never asked for")

    with _agent(real_engine, model, ran) as agent:
        turn = agent.run("Where is order 1001?", budget=Budget(model_calls=1))

    assert not turn.ok
    assert (turn.status, turn.stop_reason) == ("incomplete", "model_calls")
    assert turn.text == ""
    assert ran == []
    assert len(model.requests) == 1


def test_provider_failure_is_a_typed_turn_not_an_exception(real_engine, model):
    model.fail(503)

    with _agent(real_engine, model) as agent:
        turn = agent.run("hi")

    assert (turn.status, turn.stop_reason) == ("failed", "model_call")
    assert turn.failure["kind"] == "model_call"
    assert turn.failure["message"]
    assert len(model.requests) == 1


def test_agent_serves_a_second_turn_after_a_failed_one(real_engine, model):
    model.fail(503)
    model.say("Hello again.")

    with _agent(real_engine, model) as agent:
        first = agent.run("hi")
        second = agent.run("hi again")

    assert not first.ok
    assert second.ok and second.text == "Hello again."
    assert second.usage["model_calls"] == 1


def test_answer_is_written_to_the_wire_and_nowhere_else(real_engine, model, capfd):
    model.fail(503)
    model.say("A reply only the host should see.")

    with _agent(real_engine, model) as agent:
        agent.run("hi")
        turn = agent.run("hi again")

    captured = capfd.readouterr()
    assert turn.text == "A reply only the host should see."
    assert "only the host should see" not in captured.err
    assert "only the host should see" not in captured.out


# --- limits and checks ---------------------------------------------------------


def _limited_tools(ran):
    @tool(max_calls_per_turn=1)
    def get_order(order_number: str) -> dict:
        """Look up an order by its number."""
        ran.append(("get_order", order_number))
        return {"status": "shipped"}

    @tool(terminal=True)
    def handoff_to_human(summary: str) -> dict:
        """Pass the conversation to a person."""
        ran.append(("handoff_to_human", summary))
        return {"ticket": "T-17"}

    return [get_order, handoff_to_human]


def _limited_agent(real_engine, model, ran, **kwargs):
    return Agent(
        model="host-model",
        base_url=model.base_url,
        system_prompt=PROMPT,
        tools=_limited_tools(ran),
        engine=real_engine,
        **kwargs,
    )


def test_tool_past_its_per_turn_limit_is_not_run(real_engine, model):
    ran = []
    model.call("get_order", order_number="1001")
    model.call("get_order", order_number="2002")
    model.say("Order 1001 has shipped.")

    with _limited_agent(real_engine, model, ran) as agent:
        turn = agent.run("Where are my orders?")

    assert ran == [("get_order", "1001")]
    assert turn.ok and turn.text == "Order 1001 has shipped."
    assert turn.tool_calls[1]["arguments"] == {"order_number": "2002"}
    assert turn.tool_calls[1]["result"]["status"] == "refused"


def test_after_a_terminal_tool_no_other_tool_runs(real_engine, model):
    ran = []
    model.call("handoff_to_human", summary="empty box")
    model.call("get_order", order_number="1001")
    model.say("A colleague will email you today.")

    with _limited_agent(real_engine, model, ran) as agent:
        turn = agent.run("my box was empty")

    assert ran == [("handoff_to_human", "empty box")]
    assert turn.ok and turn.text == "A colleague will email you today."


def test_dollar_budget_stops_the_turn_at_the_agents_prices(real_engine, model):
    ran = []
    model.call("get_order", order_number="1001")
    model.say("never asked for")
    prices = Prices(input=1000.0, cached_input=0.0, output=10000.0)

    with _limited_agent(real_engine, model, ran, prices=prices) as agent:
        turn = agent.run("Where is order 1001?", budget=Budget(usd=0.10))

    # 100 in and 7 out at those prices is $0.17 for the one request.
    assert (turn.status, turn.stop_reason) == ("incomplete", "cost")
    assert turn.cost_usd == pytest.approx(0.17)
    assert ran == []
    assert len(model.requests) == 1


def test_cost_is_none_without_prices(real_engine, model):
    model.say("Hello there.")

    with _agent(real_engine, model) as agent:
        turn = agent.run("hi")

    assert turn.cost_usd is None


def handoff_was_real(answer, tool_calls):
    said = "passed this to the team" in answer.lower()
    did = any(c["name"] == "handoff_to_human" and not c["failed"] for c in tool_calls)
    if said and not did:
        return (
            "You said this was passed to the team, but handoff_to_human was not called."
        )
    return None


def test_claim_without_the_call_is_sent_back_once_then_backed_by_a_real_call(
    real_engine, model
):
    ran = []
    model.say("I've passed this to the team.")
    model.call("handoff_to_human", summary="empty box")
    model.say("I've passed this to the team.")

    with _limited_agent(
        real_engine, model, ran, answer_checks=[handoff_was_real]
    ) as agent:
        turn = agent.run("my box was empty")

    assert ran == [("handoff_to_human", "empty box")]
    assert turn.ok and turn.unresolved_checks == []
    assert turn.text == "I've passed this to the team."
    assert turn.usage["model_calls"] == 3
    repair = model.requests[1]["messages"]
    assert repair[-2] == {
        "role": "assistant",
        "content": "I've passed this to the team.",
    }
    assert "handoff_to_human was not called" in repair[-1]["content"]


def test_claim_the_model_never_backs_is_returned_for_the_host_to_withhold(
    real_engine, model
):
    model.say("I've passed this to the team.")
    model.say("I've passed this to the team.")

    with _limited_agent(
        real_engine, model, [], answer_checks=[handoff_was_real]
    ) as agent:
        turn = agent.run("my box was empty")

    assert not turn.ok
    assert (turn.status, turn.stop_reason) == ("incomplete", "unresolved_checks")
    assert turn.unresolved_checks == [
        "You said this was passed to the team, but handoff_to_human was not called."
    ]
    assert len(model.requests) == 2


def test_honest_reply_is_not_sent_back(real_engine, model):
    model.say("It shipped on Monday.")

    with _limited_agent(
        real_engine, model, [], answer_checks=[handoff_was_real]
    ) as agent:
        turn = agent.run("where is it?")

    assert turn.ok and len(model.requests) == 1
