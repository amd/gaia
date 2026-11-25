# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a host embedding the agent loop reads off a turn result.

Every test runs the real loop against a scripted model: the keys under test are
only useful if they are right on the path a host actually takes.
"""

import datetime
import json
import pathlib
import threading

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tool_outcome import REFUSED_STATUS, tool_refusal
from gaia.agents.base.tools import tool
from gaia.agents.base.turn_outcome import (
    STOP_ANSWERED,
    STOP_CANCELLED,
    STOP_MAX_STEPS,
    STOP_MODEL_CALL,
    STOP_OUTPUT_CAP,
    STOP_REPROMPT_BUDGET,
)
from gaia.eval.bench import metering
from gaia.llm import pricing
from gaia.testing import ScriptedLLMClient, ScriptExhausted, create_test_agent

REFUSAL = "That order has not been confirmed in this chat."


class _SupportAgent(Agent):
    """A chat agent whose only tools are its own, kept off the global registry."""

    def _get_system_prompt(self) -> str:
        return "You answer questions about orders."

    def _register_tools(self) -> None:
        local = {}

        @tool(registry=local)
        def get_order(order_number: str) -> dict:
            """Look up an order by its number."""
            if order_number == "1001":
                return {"status": "shipped", "carrier": "UPS"}
            return tool_refusal(REFUSAL)

        @tool(registry=local)
        def get_receipt(order_number: str) -> dict:
            """Fetch the receipt for an order."""
            return {"status": "success", "issued": datetime.date(2026, 11, 25)}

        @tool(registry=local)
        def broken_lookup(order_number: str) -> dict:
            """Look up an order in the archive."""
            return {"error": f"archive unavailable for {order_number}"}

        self._instance_tools = local


def _agent(model, **kwargs):
    return create_test_agent(_SupportAgent, llm_client=model, **kwargs)


def _sent_text(model):
    return "\n".join(
        str(message.get("content"))
        for call in model.call_history
        for message in call["messages"]
    )


def test_text_only_script_answers_through_the_real_loop():
    agent = create_test_agent(_SupportAgent, mock_responses=["Hello there."])

    result = agent.process_query("hi")

    assert result["status"] == "success"
    assert result["result"] == "Hello there."
    assert result["stop_reason"] == STOP_ANSWERED
    assert result["tool_calls"] == []
    assert result["reprompts"] == []
    assert agent._mock_llm.call_count == 1


def test_tool_call_is_run_and_recorded_with_its_result():
    model = ScriptedLLMClient()
    model.push(call="get_order", order_number="1001")
    model.push(say="It shipped with UPS.")

    result = _agent(model).process_query("Where is order 1001?")

    assert result["status"] == "success"
    assert result["result"] == "It shipped with UPS."
    assert result["tool_calls"] == [
        {
            "name": "get_order",
            "args": {"order_number": "1001"},
            "result": {"status": "shipped", "carrier": "UPS"},
            "ran": True,
            "failed": False,
        }
    ]
    assert model.remaining == 0


def test_refusal_reaches_the_model_as_an_answer_not_a_failure():
    model = ScriptedLLMClient()
    model.push(call="get_order", order_number="2002")
    model.push(say="I can't see that order until it is confirmed here.")

    result = _agent(model).process_query("Where is order 2002?")

    assert result["status"] == "success"
    assert result["error_count"] == 0
    assert result["tool_calls"][0]["failed"] is False
    assert result["tool_calls"][0]["result"] == {
        "status": REFUSED_STATUS,
        "message": REFUSAL,
    }
    sent = _sent_text(model)
    assert REFUSAL in sent
    assert "TOOL EXECUTION FAILED" not in sent


def test_bare_error_result_still_starts_error_recovery():
    model = ScriptedLLMClient()
    model.push(call="broken_lookup", order_number="2002")
    model.push(say="The archive is down, so I could not look that up.")

    result = _agent(model).process_query("Where is order 2002?")

    assert result["tool_calls"][0]["failed"] is True
    assert "TOOL EXECUTION FAILED" in _sent_text(model)


@pytest.mark.parametrize(
    "fields",
    [{"error": "boom"}, {"status": "ok"}],
)
def test_refusal_rejects_fields_that_would_change_its_meaning(fields):
    with pytest.raises(ValueError, match="tool_refusal"):
        tool_refusal("no", **fields)


def test_refusal_needs_a_reason():
    with pytest.raises(ValueError, match="non-empty"):
        tool_refusal("  ")


def test_planning_reprompt_is_reported():
    model = ScriptedLLMClient()
    model.push(say="I'll check that for you.")
    model.push(say="Order 1001 has shipped.")

    result = _agent(model).process_query("Has my order shipped?")

    assert result["result"] == "Order 1001 has shipped."
    assert [r["guard"] for r in result["reprompts"]] == ["planning"]
    assert result["reprompts"][0]["step"] == 1


def test_endless_announcing_stops_with_the_reprompt_reason():
    model = ScriptedLLMClient()
    for _ in range(8):
        model.push(say="I will now check the shipping records.")

    result = _agent(model).process_query("Has my order shipped?")

    assert result["stop_reason"] == STOP_REPROMPT_BUDGET
    assert result["reprompts"]


def test_failed_model_call_is_named_as_the_stop_reason():
    model = ScriptedLLMClient()
    model.push(raises=RuntimeError("provider returned HTTP 503"))

    result = _agent(model).process_query("hi")

    assert result["status"] == "failed"
    assert result["stop_reason"] == STOP_MODEL_CALL


def test_step_limit_is_named_as_the_stop_reason():
    model = ScriptedLLMClient()
    model.push(call="get_order", order_number="1001")
    model.push(say="It shipped with UPS.")

    result = _agent(model).process_query("Where is order 1001?", max_steps=1)

    assert result["status"] == "incomplete"
    assert result["stop_reason"] == STOP_MAX_STEPS


def test_replies_cut_off_at_the_cap_are_named_as_the_stop_reason():
    model = ScriptedLLMClient()
    for _ in range(8):
        model.push(say="The order", finish_reason="length")

    result = _agent(model).process_query("Where is order 1001?")

    assert result["status"] == "failed"
    assert result["stop_reason"] == STOP_OUTPUT_CAP


def test_extra_model_call_fails_the_test_instead_of_the_turn():
    model = ScriptedLLMClient()
    model.push(call="get_order", order_number="1001")

    with pytest.raises(ScriptExhausted):
        _agent(model).process_query("Where is order 1001?")


def test_host_cancel_is_named_as_the_stop_reason():
    model = ScriptedLLMClient(["never asked for"])
    agent = _agent(model)
    agent._cancel_event = threading.Event()
    agent._cancel_event.set()

    result = agent.process_query("hi")

    assert result["stop_reason"] == STOP_CANCELLED
    assert model.call_count == 0


def test_trace_file_is_written_when_a_tool_returns_an_object(tmp_path):
    model = ScriptedLLMClient()
    model.push(call="get_receipt", order_number="1001")
    model.push(say="Your receipt is attached.")
    agent = _agent(model, output_dir=str(tmp_path))

    result = agent.process_query("Send my receipt", trace=True)

    assert result["status"] == "success"
    assert isinstance(result["tool_calls"][0]["result"]["issued"], datetime.date)
    written = json.loads(pathlib.Path(result["output_file"]).read_text("utf-8"))
    assert written["tool_calls"][0]["result"]["issued"] == "2026-11-25"


def test_cost_is_the_rate_card_arithmetic_for_a_priced_model():
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "prompt_tokens_details": {"cached_tokens": 400},
    }
    model = ScriptedLLMClient()
    model.push(say="Hello there.", usage=usage)

    result = _agent(model, model_id="fireworks.deepseek-v4p1-flash").process_query("hi")

    assert result["input_tokens"] == 1000
    assert result["output_tokens"] == 200
    assert result["cached_tokens"] == 400
    assert result["cost_usd"] == pytest.approx(
        pricing.price("deepseek-v4p1-flash", 1000, 400, 200)
    )
    assert result["cost_usd"] > 0


def test_cost_is_none_for_a_model_with_no_list_price():
    model = ScriptedLLMClient()
    model.push(
        say="Hello there.", usage={"prompt_tokens": 1000, "completion_tokens": 200}
    )

    result = _agent(model, model_id="some-unpriced-model").process_query("hi")

    assert result["cost_usd"] is None


def test_cost_is_none_when_another_provider_serves_a_model_of_the_same_name():
    model = ScriptedLLMClient()
    model.push(
        say="Hello there.", usage={"prompt_tokens": 1000, "completion_tokens": 200}
    )

    result = _agent(model, model_id="deepseek-v4p1-flash").process_query("hi")

    assert pricing.rate_card("deepseek-v4p1-flash") is not None
    assert result["cost_usd"] is None


def test_rate_card_has_one_home():
    assert metering.RATES is pricing.RATES
    assert metering.price is pricing.price
    assert metering.rate_card is pricing.rate_card


def test_unscripted_model_call_fails_the_test_loudly():
    model = ScriptedLLMClient(["only reply"])
    assert model.chat([{"role": "user", "content": "one"}]) == "only reply"

    with pytest.raises(ScriptExhausted, match="no more"):
        model.chat([{"role": "user", "content": "two"}])


def test_push_rejects_arguments_without_a_tool():
    with pytest.raises(ValueError, match="without call="):
        ScriptedLLMClient().push(say="hi", order_number="1001")


def test_script_and_client_cannot_both_be_given():
    with pytest.raises(ValueError, match="not both"):
        create_test_agent(
            _SupportAgent, mock_responses=["hi"], llm_client=ScriptedLLMClient()
        )


def test_native_tool_call_reply_is_run_by_the_loop():
    model = ScriptedLLMClient()
    model.push(call="get_order", order_number="1001")
    model.push(say="It shipped with UPS.")
    agent = _agent(model, model_id="Gemma-4-E4B-it-GGUF")

    result = agent.process_query("Where is order 1001?")

    assert model.call_history[0]["kwargs"]["tools"], "tools were not sent natively"
    assert result["status"] == "success"
    assert result["tool_calls"][0]["result"] == {"status": "shipped", "carrier": "UPS"}
    roles = [m["role"] for m in model.call_history[1]["messages"]]
    assert roles[-2:] == ["assistant", "tool"]


def test_tool_calls_reply_needs_a_call():
    from gaia.llm.tool_calls import tool_calls_reply

    with pytest.raises(ValueError, match="at least one"):
        tool_calls_reply([])
