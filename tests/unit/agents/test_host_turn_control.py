# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""How a host steers a turn in flight, and how the loop survives a bad reply.

Every test runs the real loop against a scripted client passed through
``llm_client=``, the way a server would.
"""

import threading
import time

import pytest

from gaia.agents.base.agent import Agent, _was_all_thinking
from gaia.agents.base.budget import (
    AgentBusy,
    ModelCallContext,
    StopTurn,
    TurnBudget,
    TurnSpend,
    exhausted,
)
from gaia.agents.base.tools import tool
from gaia.agents.base.turn_outcome import (
    STOP_ANSWERED,
    STOP_CANCELLED,
    STOP_DEADLINE,
    STOP_HOST,
    STOP_MODEL_CALL,
    STOP_OUTPUT_CAP,
)
from gaia.testing import ScriptedLLMClient

CUT_OFF = {"prompt_tokens": 500, "completion_tokens": 1200}
SHORT = {"prompt_tokens": 500, "completion_tokens": 20}


class _SupportAgent(Agent):
    def __init__(self, **kwargs):
        kwargs.setdefault("silent_mode", True)
        kwargs.setdefault("host", "conversation")
        kwargs.setdefault("model_id", "scripted-model")
        super().__init__(**kwargs)

    def _get_system_prompt(self) -> str:
        return "You answer questions about orders."

    def _register_tools(self) -> None:
        local = {}

        @tool(registry=local)
        def get_order(order_number: str) -> dict:
            """Look up an order by its number."""
            return {"status": "shipped", "order": order_number}

        self._instance_tools = local


class _NativeClient(ScriptedLLMClient):
    supports_native_tools = True


class _ReasoningClient(_NativeClient):
    lowest_reasoning_effort = "low"


class RateLimited(RuntimeError):
    def __init__(self, retry_after):
        super().__init__("429")
        self.retry_after = retry_after


def _efforts(model):
    return [call["kwargs"].get("reasoning_effort") for call in model.call_history]


# A reply the loop recovered from


def test_a_reply_that_was_all_reasoning_is_retried_and_the_turn_succeeds():
    model = _ReasoningClient()
    model.push(say="", finish_reason="length", usage=CUT_OFF)
    model.push(say="It shipped.", usage=SHORT)

    result = _SupportAgent(llm_client=model, max_output_tokens=1200).process_query(
        "Where is my order?"
    )

    assert result["status"] == "success"
    assert result["stop_reason"] == STOP_ANSWERED
    assert result["result"] == "It shipped."
    assert model.call_count == 2
    assert result["output_tokens"] == 1220, "the wasted call is still counted"


def test_the_retry_after_an_all_reasoning_reply_asks_for_less_reasoning():
    model = _ReasoningClient()
    model.push(say="", finish_reason="length", usage=CUT_OFF)
    model.push(say="It shipped.", usage=SHORT)

    _SupportAgent(llm_client=model).process_query("Where is my order?")

    assert _efforts(model) == [None, "low"]
    retry_prompt = model.call_history[1]["messages"][-1]["content"]
    assert "went on thinking" in retry_prompt
    assert "file" not in retry_prompt, "a reply to a person has no file to write"


def test_no_reasoning_effort_is_sent_to_a_client_that_declares_none():
    model = _NativeClient()
    model.push(say="", finish_reason="length", usage=CUT_OFF)
    model.push(say="It shipped.", usage=SHORT)

    result = _SupportAgent(llm_client=model).process_query("Where is my order?")

    assert result["status"] == "success"
    assert _efforts(model) == [None, None]


def test_reasoning_tokens_reported_in_the_details_count_as_thinking():
    model = _ReasoningClient()
    model.push(
        say="",
        finish_reason="length",
        usage={
            "prompt_tokens": 500,
            "completion_tokens_details": {"reasoning_tokens": 1200},
        },
    )
    model.push(say="It shipped.", usage=SHORT)

    _SupportAgent(llm_client=model).process_query("Where is my order?")

    assert _efforts(model) == [None, "low"]


def test_an_empty_reply_from_a_full_window_is_not_called_thinking():
    model = _ReasoningClient()
    model.push(
        say="",
        finish_reason="length",
        usage={"prompt_tokens": 500, "completion_tokens": 0},
    )
    model.push(say="It shipped.", usage=SHORT)

    _SupportAgent(llm_client=model).process_query("Where is my order?")

    assert _efforts(model) == [None, None]
    assert "Continue from where you stopped" in (
        model.call_history[1]["messages"][-1]["content"]
    )


def test_a_reply_cut_off_mid_sentence_is_continued_and_the_turn_succeeds():
    model = _NativeClient()
    model.push(say="It shipped on", finish_reason="length", usage=CUT_OFF)
    model.push(say="It shipped on Tuesday.", usage=SHORT)

    result = _SupportAgent(llm_client=model).process_query("Where is my order?")

    assert result["status"] == "success"
    assert result["result"] == "It shipped on Tuesday."
    assert result["error_history"][0]["recovered"] is True


def test_replies_that_stay_empty_end_the_turn_with_a_reason_not_a_loop():
    model = _ReasoningClient()
    for _ in range(10):
        model.push(say="", finish_reason="length", usage=CUT_OFF)

    result = _SupportAgent(llm_client=model).process_query("Where is my order?")

    assert result["status"] == "failed"
    assert result["stop_reason"] == STOP_OUTPUT_CAP
    assert model.call_count == 3
    assert _efforts(model) == [None, "low", "low"]


# before_model_call and StopTurn


def test_the_hook_sees_what_the_turn_has_spent_before_each_request():
    seen = []
    model = _NativeClient()
    model.push(call="get_order", order_number="1", usage=SHORT)
    model.push(say="It shipped.", usage=SHORT)

    _SupportAgent(llm_client=model, before_model_call=seen.append).process_query(
        "Where is order 1?", budget=TurnBudget(deadline_s=30)
    )

    assert [type(ctx) for ctx in seen] == [ModelCallContext, ModelCallContext]
    assert [ctx.model_calls for ctx in seen] == [0, 1]
    assert [ctx.output_tokens for ctx in seen] == [0, 20]
    assert 0 < seen[1].seconds_left <= 30
    assert seen[1].elapsed_s >= seen[0].elapsed_s


def test_the_hook_reports_no_time_left_without_a_deadline():
    seen = []
    model = _NativeClient(["Hello."])

    _SupportAgent(llm_client=model, before_model_call=seen.append).process_query("Hi")

    assert seen[0].seconds_left is None


def test_stop_turn_from_the_hook_ends_the_turn_without_another_request():
    def hook(ctx):
        if ctx.model_calls == 1:
            raise StopTurn("account is over its limit")

    model = _NativeClient()
    model.push(call="get_order", order_number="1")
    model.push(say="never sent")

    result = _SupportAgent(llm_client=model, before_model_call=hook).process_query(
        "Where is order 1?"
    )

    assert result["status"] == "incomplete"
    assert result["stop_reason"] == STOP_HOST
    assert result["host_stop"] == "account is over its limit"
    assert result["failure"] is None
    assert result["error_history"] == []
    assert model.call_count == 1, "no request after the stop, and no closing one"
    assert [call["name"] for call in result["tool_calls"]] == ["get_order"]


def test_stop_turn_from_the_hook_before_the_first_request_sends_nothing():
    def hook(ctx):
        raise StopTurn("over quota")

    model = _NativeClient(["never sent"])

    result = _SupportAgent(llm_client=model, before_model_call=hook).process_query("Hi")

    assert result["stop_reason"] == STOP_HOST
    assert model.call_count == 0


def test_stop_turn_raised_by_the_client_is_a_host_stop_not_a_failure():
    model = _NativeClient()
    model.push(raises=StopTurn("provider says wait 60 s"))

    result = _SupportAgent(llm_client=model, raise_model_errors=True).process_query(
        "Hi"
    )

    assert result["status"] == "incomplete"
    assert result["stop_reason"] == STOP_HOST
    assert result["host_stop"] == "provider says wait 60 s"
    assert result["failure"] is None


def test_a_hook_that_raises_anything_else_is_a_failed_model_call():
    limited = RateLimited(retry_after=7)

    def hook(ctx):
        raise limited

    model = _NativeClient(["never sent"])
    agent = _SupportAgent(llm_client=model, before_model_call=hook)

    result = agent.process_query("Hi")

    assert result["status"] == "failed"
    assert result["stop_reason"] == STOP_MODEL_CALL
    assert agent.last_model_error is limited
    assert model.call_count == 0


def test_a_rate_limit_error_reaches_the_host_as_the_same_object():
    limited = RateLimited(retry_after=7)
    model = _NativeClient()
    model.push(raises=limited)

    with pytest.raises(RateLimited) as caught:
        _SupportAgent(llm_client=model, raise_model_errors=True).process_query("Hi")

    assert caught.value is limited
    assert caught.value.retry_after == 7


def test_the_hook_does_not_carry_into_a_turn_after_a_stop():
    stop = [True]

    def hook(ctx):
        if stop[0]:
            raise StopTurn("once")

    model = _NativeClient(["Hello again."])
    agent = _SupportAgent(llm_client=model, before_model_call=hook)

    assert agent.process_query("Hi")["stop_reason"] == STOP_HOST
    stop[0] = False
    second = agent.process_query("Hi")

    assert second["status"] == "success"
    assert "host_stop" not in second


def test_a_hook_that_is_not_callable_is_refused_when_the_agent_is_built():
    with pytest.raises(TypeError, match="before_model_call must be callable"):
        _SupportAgent(llm_client=_NativeClient(), before_model_call="nope")


# A deadline as a point in time


def test_a_deadline_already_spent_in_a_queue_sends_nothing():
    model = _NativeClient(["never sent"])

    result = _SupportAgent(llm_client=model).process_query(
        "Hi", budget=TurnBudget(deadline_at=time.monotonic() - 1)
    )

    assert result["status"] == "incomplete"
    assert result["stop_reason"] == STOP_DEADLINE
    assert model.call_count == 0


def test_a_request_is_not_started_without_room_to_finish_it():
    model = _NativeClient()
    model.push(call="get_order", order_number="1", delay_s=1.0)
    model.push(say="never sent")

    # 20 s to go and 19.5 s needed: room for the first request, none after it.
    result = _SupportAgent(llm_client=model).process_query(
        "Where is order 1?",
        budget=TurnBudget(deadline_at=time.monotonic() + 20, min_call_seconds=19.5),
    )

    assert result["stop_reason"] == STOP_DEADLINE
    assert model.call_count == 1


def test_room_to_finish_is_measured_against_what_is_left():
    budget = TurnBudget(deadline_s=10, min_call_seconds=3)

    assert exhausted(budget, TurnSpend(), elapsed_s=6.9, model=None) is None
    assert exhausted(budget, TurnSpend(), elapsed_s=7.1, model=None) == STOP_DEADLINE


def test_the_earlier_of_two_deadlines_applies():
    budget = TurnBudget(deadline_s=10, deadline_at=105.0)

    assert budget.seconds_left(elapsed_s=2, now=100.0) == 5.0
    assert budget.seconds_left(elapsed_s=8, now=100.0) == 2.0
    assert TurnBudget(max_model_calls=1).seconds_left(elapsed_s=2) is None


def test_a_hook_that_waits_past_the_deadline_sends_nothing():
    model = _NativeClient(["never sent"])

    result = _SupportAgent(
        llm_client=model, before_model_call=lambda ctx: time.sleep(0.2)
    ).process_query("Hi", budget=TurnBudget(deadline_s=0.1))

    assert result["stop_reason"] == STOP_DEADLINE
    assert model.call_count == 0


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"min_call_seconds": 1}, "needs a deadline"),
        ({"deadline_s": 5, "min_call_seconds": 0}, "positive number"),
        ({"deadline_at": "soon"}, "time.monotonic"),
        ({"deadline_at": float("nan")}, "time.monotonic"),
        ({"deadline_at": True}, "time.monotonic"),
    ],
)
def test_a_deadline_that_cannot_be_enforced_is_refused(fields, message):
    with pytest.raises(ValueError, match=message):
        TurnBudget(**fields)


# cancel()


class _CancelsMidRequest(_NativeClient):
    """Plays a host that cancels while the first request is in flight."""

    agent = None

    def chat(self, messages, model=None, stream=False, **kwargs):
        if not self.call_history:
            self.agent.cancel()
        return super().chat(messages, model=model, stream=stream, **kwargs)


def _agent_cancelled_mid_request(model):
    agent = _SupportAgent(llm_client=model)
    model.agent = agent
    return agent


def test_cancel_stops_the_turn_before_its_next_request():
    model = _CancelsMidRequest()
    model.push(call="get_order", order_number="1")
    model.push(say="never sent")

    result = _agent_cancelled_mid_request(model).process_query("Where is order 1?")

    assert result["status"] == "cancelled"
    assert result["stop_reason"] == STOP_CANCELLED
    assert result["result"] == ""
    assert model.call_count == 1


def test_cancel_from_another_thread_stops_the_turn():
    model = _NativeClient()
    model.push(call="get_order", order_number="1")
    model.push(say="never sent")
    in_request = threading.Event()
    cancelled = threading.Event()

    def hook(ctx):
        in_request.set()
        assert cancelled.wait(timeout=30)

    agent = _SupportAgent(llm_client=model, before_model_call=hook)
    results = []
    worker = threading.Thread(
        target=lambda: results.append(agent.process_query("Where is order 1?"))
    )
    worker.start()
    assert in_request.wait(timeout=30)
    agent.cancel()
    cancelled.set()
    worker.join(timeout=30)

    assert results[0]["status"] == "cancelled"
    assert model.call_count == 1


def test_cancel_with_no_turn_running_does_not_stop_the_next_one():
    model = _NativeClient(["Hello."])
    agent = _SupportAgent(llm_client=model)

    agent.cancel()
    result = agent.process_query("Hi")

    assert result["status"] == "success"


def test_an_answer_already_in_flight_when_cancel_arrives_still_stands():
    model = _CancelsMidRequest()
    model.push(say="It shipped.")
    agent = _agent_cancelled_mid_request(model)

    result = agent.process_query("Where is my order?")

    assert result["status"] == "success"
    assert result["result"] == "It shipped."
    assert agent._turn_cancel_requested is False, "a spent cancel must not linger"


def test_an_agent_answers_again_after_a_cancelled_turn():
    model = _NativeClient()
    model.push(call="get_order", order_number="1")
    model.push(say="Hello again.")
    agent = _SupportAgent(llm_client=model, before_model_call=lambda ctx: None)
    agent.before_model_call = lambda ctx: agent.cancel()

    assert agent.process_query("Where is order 1?")["status"] == "cancelled"
    agent.before_model_call = None

    assert agent.process_query("Hi")["result"] == "Hello again."


def test_a_turn_started_from_inside_a_turn_is_refused():
    model = _NativeClient(["never sent"])
    agent = _SupportAgent(llm_client=model)
    agent.before_model_call = lambda ctx: agent.process_query("nested")

    result = agent.process_query("Hi")

    assert result["status"] == "failed"
    assert isinstance(agent.last_model_error, AgentBusy)
    assert model.call_count == 0


# One agent, one turn at a time


def _hold_a_turn_open(agent, model):
    """Start a turn on another thread and park it inside its first request."""
    in_request = threading.Event()
    go_on = threading.Event()

    def hook(ctx):
        in_request.set()
        assert go_on.wait(timeout=30)

    agent.before_model_call = hook
    results = []
    worker = threading.Thread(target=lambda: results.append(agent.process_query("Hi")))
    worker.start()
    assert in_request.wait(timeout=30)

    def finish():
        go_on.set()
        worker.join(timeout=30)
        agent.before_model_call = None
        return results[0]

    return finish


def test_a_second_thread_on_a_busy_agent_is_told_so():
    model = _NativeClient(["first", "second"])
    agent = _SupportAgent(llm_client=model)
    finish = _hold_a_turn_open(agent, model)

    with pytest.raises(AgentBusy, match="Give each thread its own agent"):
        agent.process_query("Hi")

    assert finish()["result"] == "first"
    assert agent.process_query("Hi")["result"] == "second", "the claim is released"


class _RemembersTheQuestion(_SupportAgent):
    """A subclass that does its own work before the loop, as a mixin would."""

    def process_query(self, user_input, *args, **kwargs):
        self.question = user_input
        return super().process_query(user_input, *args, **kwargs)


def test_a_refused_call_does_not_reach_a_subclass_s_own_work():
    model = _NativeClient(["first"])
    agent = _RemembersTheQuestion(llm_client=model)
    finish = _hold_a_turn_open(agent, model)

    with pytest.raises(AgentBusy):
        agent.process_query("second question")

    assert agent.question == "Hi", "the refused call changed the running turn"
    assert finish()["result"] == "first"


def test_a_desktop_agent_only_warns_about_a_second_thread(caplog):
    model = _NativeClient(["first", "second"])
    agent = _SupportAgent(llm_client=None, host="workspace")
    agent.chat.llm_client = model
    finish = _hold_a_turn_open(agent, model)

    with caplog.at_level("WARNING"):
        agent.process_query("Hi")

    assert "already running on the same agent" in caplog.text
    finish()
    assert agent._turn_owner is None


def test_a_turn_that_raised_releases_the_agent():
    class Boom(RuntimeError):
        pass

    model = _NativeClient()
    model.push(raises=Boom("down"))
    model.push(say="Back.")
    agent = _SupportAgent(llm_client=model, raise_model_errors=True)

    with pytest.raises(Boom):
        agent.process_query("Hi")

    assert agent.process_query("Hi")["result"] == "Back."


def test_a_turn_refused_at_its_start_releases_the_agent():
    model = _NativeClient(["Hello."])
    agent = _SupportAgent(llm_client=model)

    with pytest.raises(TypeError):
        agent.process_query("Hi", budget="not a budget")

    assert agent.process_query("Hi")["result"] == "Hello."


# Stops and failures outside the main request


def test_stop_turn_in_the_closing_request_is_still_a_host_stop():
    model = _NativeClient()
    model.push(call="get_order", order_number="1")
    model.push(raises=StopTurn("quota"))

    result = _SupportAgent(llm_client=model, max_steps=1).process_query(
        "Where is order 1?"
    )

    assert result["status"] == "incomplete"
    assert result["stop_reason"] == STOP_HOST
    assert result["host_stop"] == "quota"


@pytest.mark.parametrize("host", ["conversation", "workspace"])
def test_a_failed_request_for_a_plan_is_a_result_not_an_exception(host):
    model = _NativeClient()
    model.push(say='{"thought": "t", "needs_plan": true}')
    model.push(raises=TimeoutError("provider slow"))
    agent = _SupportAgent(llm_client=model, host=host)

    result = agent.process_query("Where is order 1?")

    assert result["status"] == "failed"
    assert result["stop_reason"] == STOP_MODEL_CALL
    assert result["failure"]["type"] == "TimeoutError"
    assert isinstance(agent.last_model_error, TimeoutError)


def test_stop_turn_in_a_request_for_a_plan_is_a_host_stop():
    model = _NativeClient()
    model.push(say='{"thought": "t", "needs_plan": true}')
    model.push(raises=StopTurn("quota"))

    result = _SupportAgent(llm_client=model).process_query("Where is order 1?")

    assert result["stop_reason"] == STOP_HOST
    assert result["host_stop"] == "quota"


# What the model is told after a tool error


class _FailingLookupAgent(_SupportAgent):
    def _register_tools(self) -> None:
        local = {}

        @tool(registry=local)
        def get_order(order_number: str) -> dict:
            """Look up an order by its number."""
            return {"status": "error", "error": f"no order {order_number}"}

        self._instance_tools = local


def _sent_after_the_tool_error(host):
    model = _NativeClient()
    model.push(call="get_order", order_number="9999")
    model.push(say="I could not find order 9999.")
    result = _FailingLookupAgent(llm_client=model, host=host).process_query(
        "Where is order 9999?"
    )
    assert result["tool_calls"][0]["failed"] is True
    return " ".join(str(m.get("content")) for m in model.call_history[1]["messages"])


def test_a_tool_error_reaches_a_person_s_agent_without_a_recovery_plan_prompt():
    sent = _sent_after_the_tool_error("conversation")

    assert "no order 9999" in sent, "the tool's own error is what the model reads"
    assert "TOOL EXECUTION FAILED" not in sent
    assert "corrected plan" not in sent


def test_a_desktop_agent_is_still_asked_for_a_corrected_plan():
    assert "TOOL EXECUTION FAILED" in _sent_after_the_tool_error("workspace")


# Replies the protocol must keep reading the way it did


class _TextClient(ScriptedLLMClient):
    supports_native_tools = False


def test_a_tool_call_written_with_a_null_answer_still_runs_the_tool():
    model = _TextClient()
    model.push(
        say='{"thought": "t", "tool": "get_order", '
        '"tool_args": {"order_number": "1"}, "answer": null}'
    )
    model.push(say="It shipped.")

    result = _SupportAgent(llm_client=model).process_query("Where is order 1?")

    assert [call["name"] for call in result["tool_calls"]] == ["get_order"]
    assert result["result"] == "It shipped."


def test_output_tokens_alone_do_not_mean_thinking_for_a_built_in_client():
    usage = {"completion_tokens": 1200}

    assert _was_all_thinking("", None, usage) is False
    assert _was_all_thinking("", None, usage, host_client=True) is True
    assert _was_all_thinking("", None, {"reasoning_tokens": 5}) is True
    assert _was_all_thinking("text", "thought", usage, host_client=True) is False


# The scripted client's faults


def test_the_scripted_client_waits_before_it_replies_or_fails(monkeypatch):
    waits = []
    monkeypatch.setattr("gaia.testing.scripted.time.sleep", waits.append)
    model = ScriptedLLMClient()
    model.push(say="late", delay_s=2)
    model.push(raises=TimeoutError("read timed out"), delay_s=5)
    model.push(say="prompt")

    assert model.chat([{"role": "user", "content": "hi"}]) == "late"
    with pytest.raises(TimeoutError):
        model.chat([{"role": "user", "content": "hi"}])
    assert model.chat([{"role": "user", "content": "hi"}]) == "prompt"

    assert waits == [2, 5]


@pytest.mark.parametrize("host", ["conversation", "workspace"])
def test_a_reply_with_only_a_null_answer_is_asked_for_a_real_one(host):
    model = _TextClient()
    model.push(say='{"thought": "t", "answer": null}')
    model.push(say="It shipped.")

    result = _SupportAgent(llm_client=model, host=host).process_query("Where is it?")

    assert "It shipped." in result["result"]
    assert model.call_count == 2
