# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Per-tool call limits, terminal tools, a host's answer checks, and the
per-request callback. Each test runs the real loop with a scripted model."""

import re

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool
from gaia.llm import pricing
from gaia.llm.tool_calls import tool_call, tool_calls_reply
from gaia.testing import ScriptedLLMClient


class _NativeClient(ScriptedLLMClient):
    supports_native_tools = True


class _FireworksClient(_NativeClient):
    """A host client that says its model runs on Fireworks, so it is priced."""

    @property
    def provider_name(self) -> str:
        return "fireworks"


class _TextClient(ScriptedLLMClient):
    supports_native_tools = False


class _SupportAgent(Agent):
    def __init__(self, **kwargs):
        self.ran = []
        kwargs.setdefault("silent_mode", True)
        kwargs.setdefault("host", "conversation")
        kwargs.setdefault("model_id", "m")
        super().__init__(**kwargs)

    def _get_system_prompt(self) -> str:
        return "You answer questions about orders."

    def _register_tools(self) -> None:
        local = {}

        @tool(registry=local, max_calls_per_turn=1)
        def get_order(order_number: str) -> dict:
            """Look up an order by its number."""
            self.ran.append(("get_order", order_number))
            return {"status": "shipped", "order": order_number}

        @tool(registry=local, max_calls_per_turn=1, terminal=True)
        def handoff_to_human(summary: str, reason: str = "") -> dict:
            """Pass the conversation to a person."""
            self.ran.append(("handoff_to_human", summary))
            return {"status": "success", "ticket": "T-17"}

        @tool(registry=local, terminal=True)
        def broken_handoff(summary: str) -> dict:
            """Pass the conversation to a person through the old queue."""
            return {"status": "error", "error": "queue offline"}

        self._instance_tools = local


def test_second_call_past_the_limit_is_not_run_and_says_so():
    model = _NativeClient()
    model.push(call="get_order", order_number="1001")
    model.push(call="get_order", order_number="2002")
    model.push(say="Order 1001 has shipped.")
    agent = _SupportAgent(llm_client=model)

    result = agent.process_query("where are my orders?")

    assert agent.ran == [("get_order", "1001")]
    first, second = result["tool_calls"]
    assert (first["ran"], first["failed"]) == (True, False)
    assert (second["ran"], second["failed"]) == (False, False)
    # Never the first call's result under the second call's arguments.
    assert second["result"]["status"] == "refused"
    assert "already ran once this turn" in second["result"]["message"]
    assert result["status"] == "success"
    assert "TOOL EXECUTION FAILED" not in str(model.call_history[-1]["messages"])


def test_limit_is_per_turn():
    model = _NativeClient()
    for _ in range(2):
        model.push(call="get_order", order_number="1001")
        model.push(say="It has shipped.")
    agent = _SupportAgent(llm_client=model)

    agent.process_query("where is it?")
    agent.process_query("and now?")

    assert agent.ran == [("get_order", "1001")] * 2


@pytest.mark.parametrize("client", [_NativeClient, _TextClient])
def test_terminal_tool_makes_the_next_reply_the_answer_with_no_tools(client):
    model = client()
    model.push(call="handoff_to_human", summary="empty box")
    model.push(say="A colleague will email you today.")
    agent = _SupportAgent(llm_client=model)

    result = agent.process_query("my box was empty")

    assert result["result"] == "A colleague will email you today."
    assert result["status"] == "success"
    assert model.call_count == 2
    assert not model.call_history[1]["kwargs"].get("tools")
    if client is _NativeClient:
        assert model.call_history[0]["kwargs"]["tools"]


def test_tool_call_after_a_terminal_tool_is_never_run_or_shown():
    model = _NativeClient()
    model.push(call="handoff_to_human", summary="empty box")
    # A provider that returns a native call although no tools were offered.
    model.push(
        say=tool_calls_reply(
            [tool_call("get_order", {"order_number": "1001"}, "call_9")],
            content="Passing you over now.",
        )
    )
    agent = _SupportAgent(llm_client=model)

    result = agent.process_query("my box was empty")

    assert agent.ran == [("handoff_to_human", "empty box")]
    assert result["result"] == "Passing you over now."
    assert "__tool_calls__" not in result["result"]


def test_terminal_tool_that_failed_does_not_end_tool_use():
    model = _NativeClient()
    model.push(call="broken_handoff", summary="empty box")
    model.push(call="handoff_to_human", summary="empty box")
    model.push(say="A colleague will email you today.")
    agent = _SupportAgent(llm_client=model)

    result = agent.process_query("my box was empty")

    assert agent.ran == [("handoff_to_human", "empty box")]
    assert model.call_history[1]["kwargs"]["tools"]
    assert result["result"] == "A colleague will email you today."


def test_tools_are_offered_again_on_the_next_turn():
    model = _NativeClient()
    model.push(call="handoff_to_human", summary="empty box")
    model.push(say="A colleague will email you today.")
    model.push(say="Hello again.")
    agent = _SupportAgent(llm_client=model)

    agent.process_query("my box was empty")
    agent.process_query("hi")

    assert model.call_history[2]["kwargs"]["tools"]


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"max_calls_per_turn": 0}, "positive integer"),
        ({"max_calls_per_turn": True}, "positive integer"),
        ({"max_calls_per_turn": "1"}, "positive integer"),
        ({"terminal": "yes"}, "True or False"),
    ],
)
def test_bad_tool_options_are_refused(kwargs, match):
    with pytest.raises(TypeError, match=match):

        @tool(registry={}, **kwargs)
        def some_tool() -> dict:
            """Do something."""
            return {}


# --- answer checks -------------------------------------------------------------

_SAYS_HANDED_OFF = re.compile(r"passed (this|it) to (the|our) team", re.I)


def handoff_was_real(answer, tool_calls):
    """The reply may only claim a handoff the turn actually made."""
    called = any(c["name"] == "handoff_to_human" and c["ran"] for c in tool_calls)
    if _SAYS_HANDED_OFF.search(answer) and not called:
        return (
            "You said this was passed to the team, but handoff_to_human was not called."
        )
    return None


def test_claim_without_the_call_is_sent_back_once_and_then_backed_by_a_real_call():
    model = _NativeClient()
    model.push(say="I've passed this to the team.")
    model.push(call="handoff_to_human", summary="empty box")
    model.push(say="I've passed this to the team.")
    agent = _SupportAgent(llm_client=model, answer_checks=[handoff_was_real])

    result = agent.process_query("my box was empty")

    assert agent.ran == [("handoff_to_human", "empty box")]
    assert result["result"] == "I've passed this to the team."
    assert result["status"] == "success"
    assert result["unresolved_checks"] == []
    assert [r["guard"] for r in result["reprompts"]] == ["answer_check"]
    correction = model.call_history[1]["messages"][-1]["content"]
    assert "handoff_to_human was not called" in correction


def test_claim_the_model_never_backs_is_returned_for_the_host_to_withhold():
    model = _NativeClient(["I've passed this to the team."] * 2)
    agent = _SupportAgent(llm_client=model, answer_checks=[handoff_was_real])

    result = agent.process_query("my box was empty")

    assert model.call_count == 2
    assert result["status"] == "incomplete"
    assert result["result"] == "I've passed this to the team."
    assert result["unresolved_checks"] == [
        "You said this was passed to the team, but handoff_to_human was not called."
    ]
    assert result["caveats"] == result["unresolved_checks"]


def test_honest_reply_passes_the_checks_untouched():
    model = _NativeClient(["It shipped on Monday."])
    agent = _SupportAgent(llm_client=model, answer_checks=[handoff_was_real])

    result = agent.process_query("where is it?")

    assert model.call_count == 1
    assert result["reprompts"] == []
    assert result["unresolved_checks"] == []


def test_answer_checks_must_be_callable():
    with pytest.raises(TypeError, match="answer_checks"):
        _SupportAgent(llm_client=_NativeClient(), answer_checks=["not a function"])


# --- per-request callback ------------------------------------------------------


def test_on_model_call_fires_once_per_request_with_usage_and_cost():
    seen = []
    usage = {"prompt_tokens": 1000, "completion_tokens": 100}
    model = _FireworksClient()
    model.push(call="get_order", order_number="1001", usage=usage)
    model.push(say="It has shipped.", usage=usage)
    agent = _SupportAgent(
        llm_client=model, model_id="deepseek-v4p1-flash", on_model_call=seen.append
    )

    result = agent.process_query("where is it?")

    assert len(seen) == model.call_count == 2
    assert [c["finish_reason"] for c in seen] == ["tool_calls", "stop"]
    assert all(c["ok"] and c["usage"] == usage and c["seconds"] >= 0 for c in seen)
    each = pricing.price("deepseek-v4p1-flash", 1000, 0, 100)
    assert [c["cost_usd"] for c in seen] == [pytest.approx(each)] * 2
    assert result["cost_usd"] == pytest.approx(2 * each)


def test_on_model_call_gives_no_cost_for_an_unpriced_model_and_reports_failures():
    seen = []
    model = _NativeClient()
    model.push(raises=RuntimeError("HTTP 503"))
    agent = _SupportAgent(llm_client=model, on_model_call=seen.append)

    agent.process_query("hi")

    assert [(c["ok"], c["usage"], c["cost_usd"]) for c in seen] == [(False, None, None)]


def test_callback_is_disarmed_after_the_turn():
    model = _NativeClient(["Hello."])
    agent = _SupportAgent(llm_client=model, on_model_call=lambda call: None)

    agent.process_query("hi")

    assert agent.chat.after_llm_call is None
