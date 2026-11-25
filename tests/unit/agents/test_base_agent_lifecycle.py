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
    TurnCancel,
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


# ---------------------------------------------------------------------------
# The default path: the agent talks to Lemonade while it is built
# ---------------------------------------------------------------------------


def test_the_provider_is_connected_once_before_lemonade_is_probed(monkeypatch):
    """The probe learns the model's tool calling, which needs the key in place."""
    order = []
    probe = MagicMock()
    probe.base_url = "http://localhost:13305/api/v1"
    probe.health_check.side_effect = lambda: order.append("health_check")
    manager = MagicMock()
    manager.learn_tool_calling.side_effect = lambda *a: order.append("learn")
    monkeypatch.setattr(lemonade_client, "LemonadeClient", lambda **kw: probe)
    monkeypatch.setattr("gaia.llm.lemonade_manager.LemonadeManager", manager)
    monkeypatch.setattr(
        agent_module,
        "connect_hosted_provider",
        lambda provider, model, client: order.append(("connect", model, client)),
    )

    with patch("gaia.agents.base.agent.AgentSDK"):
        _PlainAgent(silent_mode=True, provider="fireworks", model_id="glm-4p6")

    assert order == [("connect", "fireworks.glm-4p6", probe), "health_check", "learn"]
    manager.ensure_ready.assert_not_called()  # a cloud model preloads nothing


class _FakeLemonade:
    """Answers the three requests a provider connect makes, and records them."""

    def __init__(self, models_after_key=26):
        self.calls = []
        self.providers = []
        self.models_after_key = models_after_key

    def request(self, method, url, json=None, **_):
        path = url.rsplit("/api/v1/", 1)[1]
        self.calls.append((method, path, json))
        body = {}
        if path == "system-info":
            body = {"cloud": {"providers": self.providers}}
        elif path == "install":
            self.providers.append({"name": json["provider"]})
        elif path == "cloud/auth":
            self.providers[0]["runtime_key_set"] = True
            self.providers[0]["models_discovered"] = self.models_after_key
            body = {"models_discovered": self.models_after_key}
        response = MagicMock(status_code=200, content=b"{}")
        response.json.return_value = body
        return response


@pytest.fixture
def fake_lemonade(monkeypatch):
    from gaia.llm import cloud_keys

    server = _FakeLemonade()
    monkeypatch.setattr(cloud_keys.requests, "request", server.request)
    monkeypatch.setattr(cloud_keys, "cloud_blocked", lambda: False)
    monkeypatch.setattr(cloud_keys, "recall_key", lambda provider: None)
    monkeypatch.setattr("gaia.llm.lemonade_choice.is_users_server", lambda url: False)
    monkeypatch.setattr(
        "gaia.llm.lemonade_launcher.gaia_runs_lemonade", lambda url=None: True
    )
    monkeypatch.setattr(
        "gaia.llm.lemonade_choice.setup_change_refusal", lambda *a: None
    )
    monkeypatch.delenv("LEMONADE_FIREWORKS_API_KEY", raising=False)
    return server


def _client():
    return SimpleNamespace(credential_base_url=lambda: "http://localhost:13305/api/v1")


def test_a_fresh_lemonade_is_sent_a_registration_then_the_key(
    fake_lemonade, monkeypatch
):
    """The requests Lemonade must accept: register Fireworks, then key it."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "fw_test_key")

    connect_hosted_provider("fireworks", "fireworks.glm-4p6", _client())

    writes = [call for call in fake_lemonade.calls if call[0] == "POST"]
    assert [path for _, path, _ in writes] == ["install", "cloud/auth"]
    registration = writes[0][2]
    assert registration["backend"] == "cloud"
    assert registration["provider"] == "fireworks"
    assert registration["wire_format"] == "openai"
    assert registration["base_url"].startswith("https://api.fireworks.ai/")
    assert writes[1][2] == {"provider": "fireworks", "api_key": "fw_test_key"}


def test_no_key_anywhere_sends_lemonade_nothing_and_raises(fake_lemonade, monkeypatch):
    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)

    with pytest.raises(HostedProviderError, match="FIREWORKS_API_KEY"):
        connect_hosted_provider("fireworks", "fireworks.glm-4p6", _client())

    assert [call for call in fake_lemonade.calls if call[0] != "GET"] == []


def test_a_key_the_provider_rejects_raises(fake_lemonade, monkeypatch):
    """Lemonade stores any key; a rejected one discovers no models."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "fw_revoked")
    fake_lemonade.models_after_key = 0

    with pytest.raises(HostedProviderError, match="no working key"):
        connect_hosted_provider("fireworks", "fireworks.glm-4p6", _client())


# ---------------------------------------------------------------------------
# Stopping a turn
# ---------------------------------------------------------------------------


def test_cancel_event_stops_the_turn_with_its_message():
    agent = _agent(["never sent"])
    stop = TurnCancel()
    stop.cancel("Stopped at your request.")
    agent.cancel_event = stop

    result = agent.process_query("question")

    assert agent.cancel_event is stop
    assert "Stopped at your request." in result["result"]
    assert agent.sent.call_count == 0


# ---------------------------------------------------------------------------
# Review findings: each of these was a bug in the first version
# ---------------------------------------------------------------------------


def test_close_runs_the_close_of_a_mixin_listed_after_agent(tmp_path):
    from gaia.database.mixin import DatabaseMixin

    class _NotesAgent(_PlainAgent, DatabaseMixin):
        pass

    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _NotesAgent(silent_mode=True, skip_lemonade=True)
    agent.init_db(str(tmp_path / "notes.db"))
    assert agent.db_ready

    agent.close()

    assert not agent.db_ready


def test_a_claude_id_given_as_model_id_is_the_model_that_runs():
    with patch("gaia.agents.base.agent.AgentSDK") as sdk:
        agent = _PlainAgent(
            silent_mode=True,
            skip_lemonade=True,
            provider="claude",
            model_id="claude-opus-5",
        )
    assert sdk.call_args.args[0].claude_model == "claude-opus-5"
    assert agent._claude_model == "claude-opus-5"


def test_two_different_claude_models_are_refused():
    with pytest.raises(ValueError, match="name two Claude models"):
        _PlainAgent(
            silent_mode=True,
            skip_lemonade=True,
            provider="claude",
            model_id="claude-opus-5",
            claude_model="claude-haiku-4-5",
        )


def test_a_local_model_id_beside_claude_keeps_the_claude_default():
    with patch("gaia.agents.base.agent.AgentSDK") as sdk:
        _PlainAgent(
            silent_mode=True,
            skip_lemonade=True,
            provider="claude",
            model_id=DEFAULT_MODEL_NAME,
        )
    assert sdk.call_args.args[0].claude_model == "claude-sonnet-5"


def test_lemonade_with_a_cloud_model_id_is_refused():
    with pytest.raises(ValueError, match="Pass provider='fireworks'"):
        resolve_agent_provider("lemonade", False, "fireworks.glm-4p6")


def test_a_gateway_error_is_reported_as_a_provider_error(monkeypatch):
    from gaia.llm.gateway import GatewayError

    def refuse(provider, client):
        raise GatewayError("Your Lemonade Server has no working AMD gateway.")

    monkeypatch.setattr("gaia.llm.cloud_keys.reconnect_provider", refuse)
    with pytest.raises(HostedProviderError, match="no working AMD gateway"):
        connect_hosted_provider("amd", "amd.Llama-3.3-70B", object())


def test_a_stand_in_chat_client_cannot_break_the_failure_label(monkeypatch):
    monkeypatch.setattr(
        lemonade_client, "configured_lemonade_url", lambda: "http://10.0.0.5:8000"
    )
    monkeypatch.setattr(
        lemonade_client, "resolve_lemonade_base_url", lambda *a: "http://10.0.0.5:8000"
    )
    agent = _agent()
    agent.chat = MagicMock()  # every attribute is another mock

    assert agent._model_service_label() == "The model server at 10.0.0.5:8000"


def test_the_history_shows_a_turn_as_soon_as_it_ends():
    agent = _agent(["Paris."])
    agent.start_session_history()

    agent.process_query("Capital of France?")

    assert [m["content"] for m in agent.conversation_history] == [
        "Capital of France?",
        "Paris.",
    ]


def test_a_turn_that_cannot_be_saved_says_so_and_leaves_no_stand_in():
    agent = _agent(["Paris."])
    history = agent.start_session_history()
    history.record = MagicMock(side_effect=OSError("disk full"))

    with pytest.raises(RuntimeError, match="could not be saved.*disk full"):
        agent.process_query("Capital of France?")

    assert history.turns(agent) == []
    assert history._pending is None


def test_a_turn_after_close_says_the_history_is_gone():
    agent = _agent(["Paris."])
    agent.start_session_history()
    agent.close()

    with pytest.raises(RuntimeError, match="closed this agent's session history"):
        agent.process_query("Capital of France?")

    assert agent.sent.call_count == 0


def test_a_new_history_after_close_works_again():
    agent = _agent(["Paris."])
    agent.start_session_history()
    agent.close()

    agent.start_session_history()
    agent.process_query("Capital of France?")

    assert len(agent.conversation_history.turns(agent)) == 1


def test_an_agent_that_never_had_a_history_still_runs_after_close():
    agent = _agent(["Paris."])
    agent.close()

    assert "Paris." in agent.process_query("Capital of France?")["result"]


def test_a_cancel_event_a_subclass_set_before_init_is_kept():
    import threading

    class _EarlyAgent(_PlainAgent):
        def __init__(self, **kwargs):
            self.cancel_event = threading.Event()
            super().__init__(**kwargs)

    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _EarlyAgent(silent_mode=True, skip_lemonade=True)

    assert isinstance(agent.cancel_event, threading.Event)


def test_cancel_event_reads_none_on_an_agent_built_without_init():
    assert _PlainAgent.__new__(_PlainAgent).cancel_event is None
