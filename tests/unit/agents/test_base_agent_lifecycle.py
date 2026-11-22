# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a custom agent built directly on the base ``Agent`` gets.

Picking a hosted provider, a failure message that names who serves the model,
tool docs after a malformed call, ``close()``, and a conversation of its own.
No Lemonade or LLM is needed: the chat client is a stub.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base import agent as agent_module
from gaia.agents.base.agent import (
    Agent,
    HostedProviderError,
    connect_hosted_provider,
    resolve_agent_provider,
)
from gaia.agents.base.history import SessionHistory
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.llm import lemonade_client
from gaia.llm.cloud_keys import CloudKeyError
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

_TOOL = "lookup_for_lifecycle_test"


class _PlainAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def lookup_for_lifecycle_test(term: str, limit: int) -> dict:
            """Look a term up.

            Args:
                term: What to look up.
                limit: How many results to return.
            """
            return {"status": "success", "term": term, "limit": limit}


@pytest.fixture(autouse=True)
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _agent(replies=(), **kwargs):
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _PlainAgent(
            silent_mode=True,
            skip_lemonade=True,
            model_id=kwargs.pop("model_id", DEFAULT_MODEL_NAME),
            **kwargs,
        )
    agent.streaming = False
    pending = list(replies)
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.send_messages = MagicMock(
        side_effect=lambda *_, **__: SimpleNamespace(text=pending.pop(0), stats={})
    )
    agent.chat = chat
    agent.sent = chat.send_messages
    return agent


# ---------------------------------------------------------------------------
# provider=
# ---------------------------------------------------------------------------


def test_no_provider_keeps_the_use_claude_switch():
    assert resolve_agent_provider(None, True, "m") == (True, "m", None)
    assert resolve_agent_provider(None, False, "m") == (False, "m", None)


def test_claude_and_lemonade_pick_their_backend():
    assert resolve_agent_provider("claude", False, None) == (True, None, None)
    assert resolve_agent_provider("Lemonade", False, "m") == (False, "m", None)


def test_a_cloud_provider_prefixes_the_model_id():
    assert resolve_agent_provider("fireworks", False, "glm-4p6") == (
        False,
        "fireworks.glm-4p6",
        "fireworks",
    )


def test_an_already_prefixed_id_is_kept():
    model = "fireworks.accounts/fireworks/models/glm-4p6"
    assert resolve_agent_provider("fireworks", False, model) == (
        False,
        model,
        "fireworks",
    )


@pytest.mark.parametrize(
    "provider,use_claude,model,match",
    [
        ("openai", False, "m", "removed"),
        ("mystery", False, "m", "Unknown provider 'mystery'"),
        ("fireworks", True, "m", "contradicts use_claude"),
        ("lemonade", True, "m", "contradicts use_claude"),
        ("fireworks", False, None, "needs model_id"),
        ("fireworks", False, "amd.Llama-3.3-70B", "names provider 'amd'"),
    ],
)
def test_a_bad_provider_choice_fails_before_anything_starts(
    provider, use_claude, model, match
):
    with pytest.raises(ValueError, match=match):
        resolve_agent_provider(provider, use_claude, model)


def test_an_unkeyed_provider_raises_naming_the_fix(monkeypatch):
    monkeypatch.setattr(
        "gaia.llm.cloud_keys.reconnect_provider", lambda provider, client: False
    )
    with pytest.raises(HostedProviderError) as raised:
        connect_hosted_provider("fireworks", "fireworks.glm-4p6", object())
    message = str(raised.value)
    assert "fireworks.glm-4p6" in message
    assert "FIREWORKS_API_KEY" in message
    assert "amd-gaia.ai/docs/" in message


def test_a_provider_error_keeps_its_cause(monkeypatch):
    def refuse(provider, client):
        raise CloudKeyError("Lemonade is not reachable at http://x:1.")

    monkeypatch.setattr("gaia.llm.cloud_keys.reconnect_provider", refuse)
    with pytest.raises(HostedProviderError, match="not reachable") as raised:
        connect_hosted_provider("amd", "amd.Llama-3.3-70B", object())
    assert isinstance(raised.value.__cause__, CloudKeyError)


def test_the_constructor_connects_the_provider_and_runs_its_model(monkeypatch):
    connected = []
    monkeypatch.setattr(
        agent_module,
        "connect_hosted_provider",
        lambda provider, model, client: connected.append((provider, model)),
    )
    agent = _agent(provider="fireworks", model_id="glm-4p6")
    assert connected == [("fireworks", "fireworks.glm-4p6")]
    assert agent.model_id == "fireworks.glm-4p6"
    assert agent._use_claude is False


def test_the_constructor_fails_loudly_when_the_provider_is_unusable(monkeypatch):
    monkeypatch.setattr(
        "gaia.llm.cloud_keys.reconnect_provider", lambda provider, client: False
    )
    with pytest.raises(HostedProviderError):
        _agent(provider="fireworks", model_id="glm-4p6")


# ---------------------------------------------------------------------------
# Failure messages name who serves the model
# ---------------------------------------------------------------------------


def test_a_cloud_model_is_named_in_the_failure_label(monkeypatch):
    monkeypatch.setattr(lemonade_client, "configured_lemonade_url", lambda: None)
    agent = _agent()
    agent.chat = SimpleNamespace(
        effective_model="fireworks.accounts/fireworks/models/glm-4p6",
        config=SimpleNamespace(use_claude=False),
        llm_client=None,
    )
    assert agent._model_service_label() == "Fireworks AI"


def test_before_the_client_exists_the_agents_own_choice_is_read():
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _PlainAgent(
            silent_mode=True,
            skip_lemonade=True,
            use_claude=True,
            claude_model="claude-sonnet-5",
        )
    del agent.chat
    location = agent._inference_location()
    assert location.provider == "claude"
    assert location.model == "claude-sonnet-5"


# ---------------------------------------------------------------------------
# A malformed call gets the tool's docs when schemas are thin
# ---------------------------------------------------------------------------


def test_a_malformed_call_gets_the_docs_once_a_turn_with_thin_schemas():
    agent = _agent()
    agent._thin_tool_schemas = lambda: True

    first = agent._execute_tool(_TOOL, {"term": "x"})
    second = agent._execute_tool(_TOOL, {"term": "x"})

    assert first["error_type"] == "invalid_arguments"
    assert first["tool_docs"].startswith(f"[Tool docs: {_TOOL}]")
    assert "limit (integer, required)" in first["tool_docs"]
    assert "tool_docs" not in second

    agent._on_task_start("next turn")
    assert "tool_docs" in agent._execute_tool(_TOOL, {"term": "x"})


def test_full_schemas_get_no_docs_the_model_already_has():
    agent = _agent()
    result = agent._execute_tool(_TOOL, {"term": "x"})
    assert result["error_type"] == "invalid_arguments"
    assert "tool_docs" not in result


# ---------------------------------------------------------------------------
# Per-conversation history and close()
# ---------------------------------------------------------------------------


def test_an_owned_history_records_each_turn_and_sends_it_next_time():
    agent = _agent(["Paris.", "About 2.1 million."])
    history = agent.start_session_history()

    agent.process_query("Capital of France?")
    agent.process_query("Its population?")

    assert isinstance(agent.conversation_history, SessionHistory)
    assert history.turns(agent)[0][0] == {
        "role": "user",
        "content": "Capital of France?",
    }
    second_request = str(agent.sent.call_args_list[1])
    assert "Capital of France?" in second_request
    assert "Paris." in second_request


def test_two_agents_keep_their_conversations_apart():
    first = _agent(["One."])
    second = _agent(["Two."])
    first.start_session_history()
    second.start_session_history()

    first.process_query("first question")

    assert second.conversation_history.turns(second) == []


def test_a_file_backed_history_resumes_in_a_new_agent(tmp_path):
    path = tmp_path / "chat.sqlite3"
    first = _agent(["Paris."])
    first.start_session_history(path)
    first.process_query("Capital of France?")
    first.close()

    second = _agent()
    second.start_session_history(path)
    assert any(
        message.get("content") == "Paris." for message in second.conversation_history
    )


def test_a_turn_that_raises_leaves_no_trace():
    agent = _agent()
    history = agent.start_session_history()
    agent._process_query_impl = MagicMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        agent.process_query("question")

    assert history.turns(agent) == []


def test_a_host_assigned_history_is_left_to_the_host():
    agent = _agent(["Answer."])
    agent.conversation_history = SessionHistory()

    agent.process_query("question")

    assert agent.conversation_history.turns(agent) == []


def test_close_releases_the_history_and_is_idempotent():
    agent = _agent()
    history = agent.start_session_history()

    agent.close()
    agent.close()

    assert agent.conversation_history == []
    with pytest.raises(Exception):
        history.turns(agent)


def test_starting_a_new_history_closes_the_previous_one():
    agent = _agent()
    old = agent.start_session_history()
    new = agent.start_session_history()

    assert agent.conversation_history is new
    with pytest.raises(Exception):
        old.turns(agent)


def test_the_agent_is_a_context_manager():
    with _agent() as agent:
        history = agent.start_session_history()
    with pytest.raises(Exception):
        history.turns(agent)
