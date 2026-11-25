# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The example in docs/sdk/embedding.mdx, run against a stand-in provider.

The guide tells a host how to plug in its own model client. These tests are
that code, so a change that breaks the documented seam fails here.
"""

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tool_outcome import tool_refusal
from gaia.agents.base.tools import tool
from gaia.llm.base_client import LLMClient
from gaia.llm.tool_calls import tool_calls_reply


class _Session:
    def verified(self, order_number):
        return order_number == "1001"

    def order(self, order_number):
        return {"number": order_number, "state": "shipped"}


class SupportAgent(Agent):
    def __init__(self, session, **kwargs):
        self.session = session
        super().__init__(
            host="conversation",
            silent_mode=True,
            max_steps=8,
            max_output_tokens=1200,
            **kwargs,
        )

    def _get_system_prompt(self) -> str:
        return "You answer questions about orders."

    def _register_tools(self) -> None:
        tools = {}

        @tool(registry=tools)
        def get_order(order_number: str) -> dict:
            """Look up an order by its number."""
            if not self.session.verified(order_number):
                return tool_refusal("That order has not been confirmed in this chat.")
            return {"status": "success", "order": self.session.order(order_number)}

        self._instance_tools = tools


class ProviderDown(RuntimeError):
    pass


class MyClient(LLMClient):
    """The guide's client, with canned OpenAI-shape responses for ``_post``."""

    provider_name = "my-provider"
    supports_native_tools = True
    context_window = 128_000

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []
        self._usage = None
        self._finish = None

    def _post(self, messages, model, tools, max_tokens):
        self.requests.append(
            {"messages": messages, "model": model, "tools": tools, "max": max_tokens}
        )
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def chat(self, messages, model=None, stream=False, **kwargs):
        response = self._post(
            messages, model, kwargs.get("tools"), kwargs["max_tokens"]
        )
        message = response["choices"][0]["message"]
        self._usage = response.get("usage")
        self._finish = response["choices"][0].get("finish_reason")
        if message.get("tool_calls"):
            return tool_calls_reply(
                message["tool_calls"], message.get("content"), self._finish
            )
        return message.get("content") or ""

    def generate(self, prompt, model=None, stream=False, **kwargs):
        return self.chat([{"role": "user", "content": prompt}], model, stream, **kwargs)

    def get_last_usage(self):
        return self._usage

    def get_last_finish_reason(self):
        return self._finish


def _text(content, usage=None):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": usage or {"prompt_tokens": 900, "completion_tokens": 40},
    }


def _call(name, arguments):
    return {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 800, "completion_tokens": 20},
    }


def _agent(responses, model_id="deepseek-v4p1-flash"):
    client = MyClient(responses)
    return SupportAgent(_Session(), llm_client=client, model_id=model_id), client


def test_host_client_gets_native_tools_and_its_tool_call_runs(caplog):
    agent, client = _agent(
        [_call("get_order", '{"order_number": "1001"}'), _text("It has shipped.")]
    )

    result = agent.process_query("Where is order 1001?")

    assert result["status"] == "success"
    assert result["result"] == "It has shipped."
    first = client.requests[0]
    assert first["model"] == "deepseek-v4p1-flash"
    assert first["max"] == 1200
    # GAIA adds read_tool_output, for results too long to send whole.
    assert [t["function"]["name"] for t in first["tools"]] == [
        "get_order",
        "read_tool_output",
    ]
    assert first["messages"][0] == {
        "role": "system",
        "content": "You answer questions about orders.",
    }
    assert result["tool_calls"][0]["result"]["order"]["state"] == "shipped"
    assert result["input_tokens"] == 1700
    assert result["output_tokens"] == 60
    # Priced only when the client says the model runs on Fireworks.
    assert result["cost_usd"] is None
    client.provider_name = "fireworks"
    assert agent._turn_cost_usd(1700, 0, 60) > 0
    assert "Failed to get stats" not in caplog.text


def test_refused_lookup_is_relayed_without_a_recovery_prompt():
    agent, client = _agent(
        [
            _call("get_order", '{"order_number": "2002"}'),
            _text("Please confirm that order in the form and I'll check it."),
        ]
    )

    result = agent.process_query("Where is order 2002?")

    assert result["status"] == "success"
    assert result["result"] == (
        "Please confirm that order in the form and I'll check it."
    )
    assert result["reprompts"] == []
    assert len(client.requests) == 2
    assert "TOOL EXECUTION FAILED" not in str(client.requests[1]["messages"])


def test_provider_error_ends_the_turn_as_a_failed_model_call():
    agent, client = _agent([ProviderDown("HTTP 503")])

    result = agent.process_query("hi")

    assert result["status"] == "failed"
    assert result["stop_reason"] == "model_call"
    assert len(client.requests) == 1


def test_prefixed_model_id_still_gets_native_tools_from_a_host_client():
    agent, client = _agent([_text("Hello.")], model_id="fireworks.not-in-catalogue")

    agent.process_query("hi")

    assert client.requests[0]["model"] == "fireworks.not-in-catalogue"
    assert client.requests[0]["tools"]


def test_guides_budget_snippet_stops_the_turn_at_its_limit():
    from gaia.agents.base.budget import TurnBudget

    calls = [_call("get_order", '{"order_number": "1001"}')] * 3
    agent, client = _agent(calls)

    result = agent.process_query("where is it?", budget=TurnBudget(max_model_calls=2))

    assert len(client.requests) == 2
    assert result["status"] == "incomplete"
    assert result["stop_reason"] == "model_calls"


def test_guides_raise_model_errors_claim():
    error = ProviderDown("HTTP 503")
    client = MyClient([error])
    agent = SupportAgent(
        _Session(), llm_client=client, model_id="m", raise_model_errors=True
    )

    with pytest.raises(ProviderDown) as caught:
        agent.process_query("hi")

    assert caught.value is error
    assert len(client.requests) == 1


@pytest.mark.parametrize("extra", ["temperature", "tool_choice", "stop"])
def test_client_that_takes_kwargs_is_never_broken_by_an_extra_one(extra):
    client = MyClient([_text("Hello.")])

    assert client.chat([], max_tokens=10, **{extra: "x"}) == "Hello."


def test_guides_testing_snippet_runs():
    from gaia.testing import ScriptedLLMClient

    model = ScriptedLLMClient()
    model.push(call="get_order", order_number="1001")
    model.push(say="It shipped on Monday.")
    agent = SupportAgent(_Session(), llm_client=model, model_id="any-name")

    result = agent.process_query("Where is order 1001?")

    assert result["result"] == "It shipped on Monday."


def test_scope_from_the_guide_still_allows_paging_through_a_long_result():
    agent = SupportAgent(
        _Session(),
        llm_client=MyClient([]),
        model_id="m",
        tool_scope=["get_order", "read_tool_output"],
    )

    assert agent._tool_scope == {"get_order", "read_tool_output"}


def test_guides_answer_check_snippet_withholds_an_unbacked_claim():
    def handoff_was_real(answer, tool_calls):
        said = "passed this to the team" in answer.lower()
        did = any(c["name"] == "handoff_to_human" and c["ran"] for c in tool_calls)
        if said and not did:
            return "You said this was passed to the team, but handoff_to_human was not called."
        return None

    client = MyClient([_text("I've passed this to the team.")] * 2)
    agent = SupportAgent(
        _Session(), llm_client=client, model_id="m", answer_checks=[handoff_was_real]
    )

    result = agent.process_query("my box was empty")

    assert result["status"] == "incomplete"
    assert len(result["unresolved_checks"]) == 1


def test_guides_on_model_call_snippet_reports_each_request():
    seen = []
    client = MyClient([_text("Hello.")])
    agent = SupportAgent(
        _Session(), llm_client=client, model_id="m", on_model_call=seen.append
    )

    agent.process_query("hi")

    assert len(seen) == 1
    assert set(seen[0]) == {"usage", "ok", "seconds", "finish_reason", "cost_usd"}
