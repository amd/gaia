# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A host's own model client, its errors, and the limits it sets on a turn.

Every test runs the real loop. The client is passed through ``llm_client=``,
the way a server would, never patched in afterwards.
"""

import time

import pytest

from gaia.agents.base.agent import CLOUD_MAX_OUTPUT_TOKENS, Agent
from gaia.agents.base.budget import BudgetNeedsUsage, TurnBudget
from gaia.agents.base.tools import tool
from gaia.agents.base.turn_outcome import (
    STOP_ANSWERED,
    STOP_COST,
    STOP_DEADLINE,
    STOP_MODEL_CALL,
    STOP_MODEL_CALLS,
    STOP_OUTPUT_TOKENS,
)
from gaia.testing import ScriptedLLMClient

PRICED = "deepseek-v4p1-flash"
USAGE = {"prompt_tokens": 1000, "completion_tokens": 100}


class _SupportAgent(Agent):
    def __init__(self, **kwargs):
        kwargs.setdefault("silent_mode", True)
        kwargs.setdefault("host", "conversation")
        super().__init__(**kwargs)

    def _get_system_prompt(self) -> str:
        return "You answer questions about orders."

    def _register_tools(self) -> None:
        local = {}

        @tool(registry=local)
        def get_order(order_number: str) -> dict:
            """Look up an order by its number."""
            return {"status": "shipped", "order": order_number}

        @tool(registry=local)
        def slow_lookup(order_number: str) -> dict:
            """Look up an order in the slow archive."""
            time.sleep(0.2)
            return {"status": "archived", "order": order_number}

        self._instance_tools = local


class _NativeClient(ScriptedLLMClient):
    supports_native_tools = True
    context_window = 128_000


class _FireworksClient(_NativeClient):
    """A host client that says its model runs on Fireworks, so it is priced."""

    @property
    def provider_name(self) -> str:
        return "fireworks"


class _TextClient(ScriptedLLMClient):
    supports_native_tools = False


class ProviderDown(RuntimeError):
    pass


class ProviderUnreachable(ConnectionError):
    pass


def _lookups(model, count):
    for number in range(count):
        model.push(call="get_order", order_number=str(1000 + number), usage=USAGE)
    return model


# --- the client is the host's --------------------------------------------------


def test_no_lemonade_code_runs_for_a_host_client(monkeypatch):
    import gaia.llm.lemonade_client as lemonade_client
    import gaia.llm.lemonade_manager as lemonade_manager

    def _refuse(*args, **kwargs):
        raise AssertionError("a host client must not reach Lemonade")

    monkeypatch.setattr(lemonade_client.LemonadeClient, "__init__", _refuse)
    monkeypatch.setattr(lemonade_manager.LemonadeManager, "ensure_ready", _refuse)
    model = _NativeClient(["Hello there."])

    agent = _SupportAgent(llm_client=model, model_id="my-model")
    result = agent.process_query("hi")

    assert agent.chat.llm_client is model
    assert result["status"] == "success"
    assert model.call_history[0]["model"] == "my-model"


def test_tool_format_comes_from_each_client_not_a_shared_catalogue():
    native = _NativeClient(["Hello."])
    text = _TextClient(["Hello."])
    first = _SupportAgent(llm_client=native, model_id="fireworks.model-a")
    second = _SupportAgent(llm_client=text, model_id="fireworks.model-b")

    first.process_query("hi")
    second.process_query("hi")

    assert [t["function"]["name"] for t in native.call_history[0]["kwargs"]["tools"]][
        0
    ] == "get_order"
    assert "get_order" not in native.call_history[0]["messages"][0]["content"]
    assert not text.call_history[0]["kwargs"].get("tools")
    assert "get_order" in text.call_history[0]["messages"][0]["content"]


def test_context_window_is_read_from_the_client():
    from gaia.agents.base.history import context_window

    agent = _SupportAgent(llm_client=_NativeClient(), model_id="my-model")

    assert context_window(agent) == 128_000


def test_no_chat_template_stop_tokens_are_sent_to_a_host_client():
    model = _NativeClient(["Hello."])

    _SupportAgent(llm_client=model, model_id="qwen-hosted").process_query("hi")

    assert "stop" not in model.call_history[0]["kwargs"]


@pytest.mark.parametrize(
    "kwargs, error, match",
    [
        ({"llm_client": object(), "model_id": "m"}, TypeError, "LLMClient"),
        ({"llm_client": _NativeClient()}, ValueError, "needs model_id"),
        (
            {"llm_client": _NativeClient(), "model_id": "m", "use_claude": True},
            ValueError,
            "not both",
        ),
        (
            {"skip_lemonade": True, "budget": {"max_model_calls": 2}},
            TypeError,
            "budget",
        ),
    ],
)
def test_bad_construction_is_refused(kwargs, error, match):
    with pytest.raises(error, match=match):
        _SupportAgent(**kwargs)


# --- its errors are its own ----------------------------------------------------


@pytest.mark.parametrize("exc", [ProviderDown("HTTP 503"), ProviderUnreachable("dns")])
def test_raise_model_errors_gives_the_caller_the_clients_own_exception(exc):
    model = _NativeClient()
    model.push(raises=exc)
    agent = _SupportAgent(llm_client=model, model_id="m", raise_model_errors=True)

    with pytest.raises(type(exc)) as caught:
        agent.process_query("hi")

    assert caught.value is exc
    assert model.call_count == 1


@pytest.mark.parametrize("exc", [ProviderDown("HTTP 503"), ProviderUnreachable("dns")])
def test_without_it_the_failure_is_typed_on_the_result(exc):
    model = _NativeClient()
    model.push(raises=exc)
    agent = _SupportAgent(llm_client=model, model_id="m")

    result = agent.process_query("hi")

    assert result["status"] == "failed"
    assert result["stop_reason"] == STOP_MODEL_CALL
    assert result["failure"] == {
        "kind": "model_call",
        "type": type(exc).__name__,
        "message": str(exc),
        "step": 1,
    }
    assert agent.last_model_error is exc


def test_a_good_turn_has_no_failure():
    agent = _SupportAgent(llm_client=_NativeClient(["Hello."]), model_id="m")

    result = agent.process_query("hi")

    assert result["failure"] is None
    assert agent.last_model_error is None


def test_failure_survives_the_trace_file(tmp_path):
    model = _NativeClient()
    model.push(raises=ProviderDown("HTTP 503"))
    agent = _SupportAgent(llm_client=model, model_id="m", output_dir=str(tmp_path))

    result = agent.process_query("hi", trace=True)

    assert result["failure"]["type"] == "ProviderDown"
    assert (tmp_path / result["output_file"].split("\\")[-1].split("/")[-1]).exists()


# --- its limits hold -----------------------------------------------------------


def test_model_call_limit_stops_the_turn_with_no_closing_call():
    model = _lookups(_NativeClient(), 5)
    agent = _SupportAgent(llm_client=model, model_id="m")

    result = agent.process_query(
        "check my orders", budget=TurnBudget(max_model_calls=2)
    )

    assert model.call_count == 2
    assert result["status"] == "incomplete"
    assert result["stop_reason"] == STOP_MODEL_CALLS
    assert result["budget_spent"]["model_calls"] == 2
    for call in model.call_history:
        users = [m["content"] for m in call["messages"] if m["role"] == "user"]
        assert users == ["check my orders"]


def test_output_token_limit_caps_each_request_and_then_stops():
    model = _lookups(_NativeClient(), 5)
    agent = _SupportAgent(llm_client=model, model_id="m", max_output_tokens=120)

    result = agent.process_query(
        "check my orders", budget=TurnBudget(max_output_tokens=150)
    )

    assert [c["kwargs"]["max_tokens"] for c in model.call_history] == [120, 50]
    assert result["stop_reason"] == STOP_OUTPUT_TOKENS
    assert result["budget_spent"]["output_tokens"] == 200


def test_cost_limit_stops_the_turn():
    model = _lookups(_FireworksClient(), 5)
    agent = _SupportAgent(llm_client=model, model_id=PRICED)

    result = agent.process_query(
        "check my orders", budget=TurnBudget(max_cost_usd=0.0005)
    )

    # 1000 in + 100 out is $0.00042 a call at the card, so the second call ends it.
    assert model.call_count == 2
    assert result["stop_reason"] == STOP_COST
    assert result["cost_usd"] == pytest.approx(0.00084)


def test_cost_limit_on_another_providers_model_is_refused_before_any_call():
    model = _NativeClient(["Hello."])
    agent = _SupportAgent(llm_client=model, model_id=PRICED)

    with pytest.raises(ValueError, match="list prices only"):
        agent.process_query("hi", budget=TurnBudget(max_cost_usd=0.01))

    assert model.call_count == 0


def test_cost_limit_on_an_unpriced_model_is_refused_before_any_call():
    model = _FireworksClient(["Hello."])
    agent = _SupportAgent(llm_client=model, model_id="no-such-model")

    with pytest.raises(ValueError, match="no price on the rate card"):
        agent.process_query("hi", budget=TurnBudget(max_cost_usd=0.01))

    assert model.call_count == 0


def test_deadline_stops_the_turn_between_model_calls():
    model = _NativeClient()
    model.push(call="slow_lookup", order_number="1001")
    model.push(say="never reached")
    agent = _SupportAgent(llm_client=model, model_id="m")

    result = agent.process_query("where is it?", budget=TurnBudget(deadline_s=0.1))

    assert model.call_count == 1
    assert result["stop_reason"] == STOP_DEADLINE
    assert result["status"] == "incomplete"


def test_a_turn_inside_its_budget_answers_normally():
    model = _lookups(_NativeClient(), 1)
    model.push(say="It shipped.", usage=USAGE)
    agent = _SupportAgent(
        llm_client=model, model_id="m", budget=TurnBudget(max_model_calls=8)
    )

    result = agent.process_query("where is it?")

    assert result["status"] == "success"
    assert result["stop_reason"] == STOP_ANSWERED
    assert result["result"] == "It shipped."
    assert result["budget_spent"] == {
        "model_calls": 2,
        "input_tokens": 2000,
        "cached_tokens": 0,
        "output_tokens": 200,
    }


def test_budget_does_not_carry_into_the_next_turn():
    model = _lookups(_NativeClient(), 2)
    model.push(say="Hello again.")
    agent = _SupportAgent(llm_client=model, model_id="m")

    agent.process_query("check my orders", budget=TurnBudget(max_model_calls=1))
    result = agent.process_query("hi")

    assert "budget_spent" not in result
    assert result["stop_reason"] != STOP_MODEL_CALLS


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_model_calls": 0},
        {"max_output_tokens": -5},
        {"deadline_s": 0},
        {"max_cost_usd": True},
        {"max_model_calls": 1.5},
    ],
)
def test_budget_rejects_limits_that_are_not_positive(kwargs):
    with pytest.raises(ValueError, match="TurnBudget"):
        TurnBudget(**kwargs)


# --- found in review -----------------------------------------------------------


def test_budget_is_disarmed_once_the_turn_ends():
    model = _lookups(_NativeClient(), 1)
    model.push(say="later, outside any turn")
    agent = _SupportAgent(
        llm_client=model, model_id="m", budget=TurnBudget(max_model_calls=1)
    )

    result = agent.process_query("check my orders")

    assert result["stop_reason"] == STOP_MODEL_CALLS
    assert agent.chat.before_llm_call is None
    assert agent.chat.after_llm_call is None
    # A warm-up or a direct call after the turn is not that turn's spend.
    reply = agent.chat.send_messages([{"role": "user", "content": "ping"}])
    assert reply.text == "later, outside any turn"


def test_refused_budget_leaves_nothing_armed_from_the_turn_before():
    model = _NativeClient(["Hello.", "Hello again."])
    agent = _SupportAgent(llm_client=model, model_id="no-such-model")
    agent.process_query("hi", budget=TurnBudget(max_model_calls=5))

    with pytest.raises(ValueError, match="cannot be priced"):
        agent.process_query("hi", budget=TurnBudget(max_cost_usd=0.01))

    assert agent.chat.before_llm_call is None
    assert agent.process_query("hi")["result"] == "Hello again."


def test_token_limit_without_reported_usage_raises_instead_of_looking_like_an_outage():
    model = _NativeClient(["Hello there."])
    agent = _SupportAgent(llm_client=model, model_id="m")

    with pytest.raises(BudgetNeedsUsage, match="get_last_usage"):
        agent.process_query("hi", budget=TurnBudget(max_output_tokens=500))

    assert agent.chat.before_llm_call is None


def test_call_and_time_limits_need_no_usage():
    model = _NativeClient(["Hello there."])
    agent = _SupportAgent(llm_client=model, model_id="m")

    result = agent.process_query(
        "hi", budget=TurnBudget(max_model_calls=3, deadline_s=30)
    )

    assert result["status"] == "success"


def test_reply_cut_short_by_the_budgets_own_cap_is_a_budget_stop():
    model = _NativeClient()
    model.push(call="get_order", order_number="1001", usage=USAGE)
    model.push(
        say="Your order",
        finish_reason="length",
        usage={"prompt_tokens": 1000, "completion_tokens": 50},
    )
    model.push(say="never reached")
    agent = _SupportAgent(llm_client=model, model_id="m")

    result = agent.process_query(
        "where is it?", budget=TurnBudget(max_output_tokens=150)
    )

    assert model.call_count == 2
    assert result["status"] == "incomplete"
    assert result["stop_reason"] == STOP_OUTPUT_TOKENS
    assert result["failure"] is None


def test_step_limit_closing_reply_that_is_a_tool_call_is_not_raised_as_a_client_error():
    model = _lookups(_NativeClient(), 6)
    agent = _SupportAgent(llm_client=model, model_id="m", raise_model_errors=True)

    result = agent.process_query("check my orders", max_steps=2)

    assert result["status"] == "incomplete"
    assert result["failure"] is None


@pytest.mark.parametrize(
    "model_id", ["deepseek-v4p1-flash", "fireworks.some-model", "Gemma-4-E4B-it-GGUF"]
)
def test_the_shape_of_the_model_id_changes_nothing_for_a_host_client(model_id):
    model = _NativeClient(["Hello."])
    agent = _SupportAgent(llm_client=model, model_id=model_id)

    agent.process_query("hi")

    sent = model.call_history[0]
    assert sent["model"] == model_id
    assert sent["kwargs"]["max_tokens"] == CLOUD_MAX_OUTPUT_TOKENS
    assert sent["kwargs"]["tools"]
    assert "reasoning_effort" not in sent["kwargs"]
    assert agent.model_features.scratchpad is True
    assert agent._hosted_model() is True
    assert agent._is_loaded_ctx_too_small() is False
