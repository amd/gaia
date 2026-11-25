# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``Agent(host="conversation")``: the reply goes to an end user as written.

The default host treats a request as work on a workspace: it reads the user's
words for files, sends back a reply that sounds like a plan, and appends what
it could not confirm. Each of those broke a chat agent whose replies go to a
customer, so every case here is run in both modes.
"""

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool
from gaia.agents.base.turn_outcome import STOP_ANSWERED, STOP_TOOL_CALL_FORMAT
from gaia.testing import ScriptedLLMClient, create_test_agent


class _SupportAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "You answer questions about orders."

    def _register_tools(self) -> None:
        local = {}

        @tool(registry=local)
        def get_order(order_number: str) -> dict:
            """Look up an order by its number."""
            return {"status": "shipped", "total": "$49.99"}

        @tool(registry=local)
        def create_ticket(summary: str) -> dict:
            """Open a support ticket for a human to pick up."""
            return {"status": "success", "ticket": "T-17"}

        self._instance_tools = local


def _run(replies, message, host, calls=()):
    model = ScriptedLLMClient()
    for name, args in calls:
        model.push(call=name, **args)
    for reply in replies:
        model.push(say=reply)
    agent = create_test_agent(_SupportAgent, llm_client=model, host=host)
    return agent.process_query(message), model


FORM_REPLY = (
    "Please confirm the order in the form and I'll check its status right away."
)


def test_reply_that_sounds_like_a_plan_is_the_answer():
    result, model = _run([FORM_REPLY], "Where is my order?", "conversation")

    assert result["status"] == "success"
    assert result["result"] == FORM_REPLY
    assert result["stop_reason"] == STOP_ANSWERED
    assert result["reprompts"] == []
    assert model.call_count == 1


def test_workspace_host_still_sends_that_reply_back():
    result, model = _run(
        [FORM_REPLY, "Your order shipped."], "Where is my order?", "workspace"
    )

    assert [r["guard"] for r in result["reprompts"]] == ["planning"]
    assert model.call_count == 2


@pytest.mark.parametrize(
    "reply",
    [
        "I will now check the shipping records.",
        "Let me look into that for you.",
        "I'll verify that with the warehouse and we'll look at the options.",
    ],
)
def test_announced_step_is_not_replaced_or_sent_back(reply):
    result, model = _run([reply], "Has it shipped?", "conversation")

    assert result["result"] == reply
    assert result["status"] == "success"
    assert model.call_count == 1


PATH_LIKE_MESSAGES = [
    "went to store 1836 and store 1136 had a box dated 10/15 and it was EMPTY!",
    "save a summary of this to report.txt, I never got my refund",
    "the label said C:/returns/box7 and nobody could find it",
]


@pytest.mark.parametrize("message", PATH_LIKE_MESSAGES)
def test_path_like_words_from_the_user_are_not_a_work_order(message):
    reply = "Sorry about that. I've passed this to the team."

    result, model = _run(
        [reply],
        message,
        "conversation",
        calls=[("create_ticket", {"summary": "empty box"})],
    )

    assert result["status"] == "success"
    assert result["result"] == reply
    assert result["completion_gaps"] == []
    assert result["completion_caveats"] == []
    assert result["caveats"] == []
    assert model.call_count == 2


@pytest.mark.parametrize("message", PATH_LIKE_MESSAGES[:2])
def test_workspace_host_still_reports_the_unwritten_file(message):
    result, _ = _run(["Sorry about that."], message, "workspace")

    assert result["status"] == "incomplete"
    assert "No successful write to" in result["result"]


@pytest.mark.parametrize(
    "reply",
    [
        "Your order total was $49.99 and it shipped on 10/15.",
        "I checked the order and it has shipped.",
        "I've saved the file and all 12 tests passed on our side.",
        "I deleted the old address from your account.",
    ],
)
def test_reply_is_returned_byte_for_byte_after_a_tool_call(reply):
    result, model = _run(
        [reply],
        "what did I pay?",
        "conversation",
        calls=[("get_order", {"order_number": "1001"})],
    )

    assert result["result"] == reply
    assert result["status"] == "success"
    assert result["completion_gaps"] == []
    assert model.call_count == 2


def test_text_beside_a_tool_call_is_not_prepended_to_the_reply():
    model = ScriptedLLMClient()
    model.push(say="One moment.", call="create_ticket", summary="empty box")
    model.push(say="A colleague will email you today.")
    agent = create_test_agent(
        _SupportAgent,
        llm_client=model,
        host="conversation",
        model_id="Gemma-4-E4B-it-GGUF",
    )

    result = agent.process_query("my box was empty")

    assert result["result"] == "A colleague will email you today."


def test_time_limit_does_not_change_the_users_message():
    model = ScriptedLLMClient(["Hello there."])
    agent = create_test_agent(_SupportAgent, llm_client=model, host="conversation")
    agent.time_limit_s = 25

    agent.process_query("hi")

    users = [m for m in model.call_history[0]["messages"] if m["role"] == "user"]
    assert [m["content"] for m in users] == ["hi"]


def test_fake_tool_output_is_still_sent_back_but_only_so_many_times():
    fake = 'Here you go: {"status": "success", "order": "1001"}'
    model = ScriptedLLMClient([fake] * 12)
    agent = create_test_agent(_SupportAgent, llm_client=model, host="conversation")

    result = agent.process_query("where is it?", max_steps=12)

    assert [r["guard"] for r in result["reprompts"]] == ["fake_json", "fake_json"]
    assert model.call_count == 3
    assert result["status"] == "incomplete"
    assert result["stop_reason"] == STOP_TOOL_CALL_FORMAT
    assert result["caveats"] == result["completion_gaps"] != []


def test_unknown_host_is_refused():
    with pytest.raises(ValueError, match="host must be one of"):
        create_test_agent(_SupportAgent, mock_responses=["hi"], host="chat")


def test_reply_with_tool_markup_is_never_returned_as_a_success():
    markup = "I ran <function_calls> for you and it is on the way."
    model = ScriptedLLMClient([markup] * 3)
    agent = create_test_agent(_SupportAgent, llm_client=model, host="conversation")

    result = agent.process_query("where is it?")

    assert [r["guard"] for r in result["reprompts"]] == ["tool_markup", "tool_markup"]
    assert result["status"] == "incomplete"
    assert result["stop_reason"] == STOP_TOOL_CALL_FORMAT


class _PickyAgent(_SupportAgent):
    """A host's own answer check, which the conversation host still runs."""

    def answer_problems(self, answer):
        return [] if "ticket" in answer else ["The reply must name the ticket."]


def test_repaired_reply_replaces_the_draft_instead_of_joining_it():
    draft = "A colleague will look into the empty box and get back to you by email."
    model = ScriptedLLMClient([draft, "Ticket T-17."])
    agent = create_test_agent(_PickyAgent, llm_client=model, host="conversation")

    result = agent.process_query("my box was empty")

    assert result["result"] == "Ticket T-17."
    assert [r["guard"] for r in result["reprompts"]] == ["answer_check"]


def test_unrepaired_reply_keeps_its_text_and_lists_the_problem_beside_it():
    model = ScriptedLLMClient(["We are on it.", "We are still on it."])
    agent = create_test_agent(_PickyAgent, llm_client=model, host="conversation")

    result = agent.process_query("my box was empty")

    assert result["result"] == "We are still on it."
    assert result["caveats"] == ["The reply must name the ticket."]
    assert result["status"] == "incomplete"


def test_cancelled_turn_has_the_keys_a_host_reads():
    import threading

    model = ScriptedLLMClient(["Hello there."])
    agent = create_test_agent(_SupportAgent, llm_client=model, host="conversation")

    class _Console:
        cancelled = threading.Event()

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    agent.console = _Console()
    agent.console.cancelled.set()
    agent.streaming = True

    result = agent.process_query("hi")

    assert result["status"] == "cancelled"
    assert result["caveats"] == []
    assert result["tool_calls"] == []
    assert result["stop_reason"] == "cancelled"
