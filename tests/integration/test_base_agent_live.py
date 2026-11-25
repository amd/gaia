# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A plain ``Agent`` subclass against a real Lemonade Server.

Unit tests stub the chat client, so they cannot show that a recorded turn is
really sent back, that a model really calls a tool through it, or that Lemonade
accepts the hosted-provider path. These do.

Every test skips via ``require_lemonade`` when no server is running. The hosted
tests also skip unless that server has a working Fireworks key; set
``GAIA_TEST_FIREWORKS_MODEL`` to pick the model (default: the first one listed).
"""

import os

import pytest

from gaia.agents.base.agent import Agent, TurnCancel
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.tools.user_input_tools import UserInputToolsMixin

pytestmark = pytest.mark.integration

_CALLS = []


class _AddAgent(Agent):
    def _get_system_prompt(self) -> str:
        return (
            "You are a terse assistant. Use the add tool for any addition. "
            "Answer in one short sentence."
        )

    def _register_tools(self) -> None:
        @tool
        def add(a: int, b: int) -> dict:
            """Add two integers.

            Args:
                a: First number.
                b: Second number.
            """
            _CALLS.append((a, b))
            return {"status": "success", "sum": a + b}


@pytest.fixture(autouse=True)
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    _CALLS.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _fireworks_model() -> str:
    """A Fireworks model the running Lemonade can serve, or skip."""
    from gaia.llm.cloud_keys import CloudKeyError, _provider_entry, provider_usable
    from gaia.llm.lemonade_client import LemonadeClient

    client = LemonadeClient(verbose=False)
    try:
        entry = _provider_entry(client.credential_base_url(), "fireworks")
    except CloudKeyError as e:
        pytest.skip(f"Could not read Lemonade's cloud providers: {e}")
    if not provider_usable(entry):
        pytest.skip("Lemonade has no working Fireworks key")
    chosen = os.environ.get("GAIA_TEST_FIREWORKS_MODEL")
    if chosen:
        return chosen
    listed = [
        model["id"]
        for model in client.list_models(show_all=True).get("data", [])
        if model["id"].startswith("fireworks.")
    ]
    if not listed:
        pytest.skip("Lemonade lists no Fireworks models")
    return listed[0]


def _converse(make, tmp_path):
    """Two turns, a close, a resume, and an agent that was never told."""
    path = tmp_path / "chat.sqlite3"
    with make() as agent:
        agent.start_session_history(path)
        first = agent.process_query("My name is Sam. What is 17 + 25?")["result"]
        assert (17, 25) in _CALLS, first
        assert "42" in first
        second = agent.process_query("What is my name, and what was that sum?")
        assert "Sam" in second["result"] and "42" in second["result"]
    assert agent.conversation_history == []

    with make() as resumed:
        resumed.start_session_history(path)
        assert "Sam" in resumed.process_query("What is my name?")["result"]

    with make() as stranger:
        assert "Sam" not in stranger.process_query("What is my name?")["result"]


def test_a_local_conversation_is_remembered_resumed_and_kept_apart(
    require_lemonade, tmp_path
):
    _converse(lambda: _AddAgent(silent_mode=True), tmp_path)


def test_a_set_cancel_event_ends_the_turn_without_calling_the_model(
    require_lemonade,
):
    with _AddAgent(silent_mode=True) as agent:
        stop = TurnCancel()
        stop.cancel("Stopped at your request.")
        agent.cancel_event = stop
        assert "Stopped at your request." in agent.process_query("2 + 3?")["result"]
    assert _CALLS == []


def test_a_failed_local_turn_names_the_server(require_lemonade):
    with _AddAgent(
        silent_mode=True, skip_lemonade=True, model_id="no-such-model-GGUF"
    ) as agent:
        result = agent.process_query("hi")
    assert result["model_failed"] is True
    assert "Lemonade on this machine" in result["result"]


def test_a_hosted_conversation_runs_on_the_named_provider(require_lemonade, tmp_path):
    model = _fireworks_model()

    def make():
        return _AddAgent(silent_mode=True, provider="fireworks", model_id=model)

    with make() as agent:
        assert agent.model_id == model
        assert agent._model_service_label() == "Fireworks AI"
    _converse(make, tmp_path)


def test_an_unprefixed_hosted_model_gets_its_prefix(require_lemonade):
    model = _fireworks_model()
    bare = model.split(".", 1)[1]
    with _AddAgent(silent_mode=True, provider="fireworks", model_id=bare) as agent:
        assert agent.model_id == model


def test_a_contradicting_provider_fails_before_lemonade_is_touched():
    with pytest.raises(ValueError, match="names provider 'amd'"):
        _AddAgent(silent_mode=True, provider="fireworks", model_id="amd.Llama-3.3-70B")


def _second_local_model(current: str) -> str:
    """Another downloaded local chat model to switch to (a small one first), or skip."""
    from gaia.agents.base.model_switch import _lemonade_models, _lemonade_url
    from gaia.llm.lemonade_client import cloud_model_provider

    with _AddAgent(silent_mode=True) as probe:
        listed = _lemonade_models(_lemonade_url(probe))
    others = [
        name
        for name, entry in listed.items()
        if name != current and cloud_model_provider(name, entry) is None
    ]
    if not others:
        pytest.skip("Lemonade has only one downloaded chat model")
    small = [name for name in others if "Gemma-4-E" in name or "Qwen3.5" in name]
    return (small or others)[0]


def test_a_conversation_survives_a_switch_to_another_local_model(require_lemonade):
    with _AddAgent(silent_mode=True) as agent:
        first = agent.chat.effective_model
        other = _second_local_model(first)
        agent.start_session_history()
        agent.process_query("My name is Sam. What is 17 + 25?")

        agent.switch_model(other)

        assert agent.model_id == other
        assert agent.chat.effective_model == other
        answer = agent.process_query("What is my name, and what was that sum?")
        assert "Sam" in answer["result"] and "42" in answer["result"]


def test_a_conversation_survives_a_switch_to_a_hosted_model_and_back(
    require_lemonade,
):
    hosted = _fireworks_model()
    with _AddAgent(silent_mode=True) as agent:
        local = agent.chat.effective_model
        agent.start_session_history()
        agent.process_query("My name is Sam. What is 17 + 25?")

        agent.switch_model(hosted)
        assert agent._model_service_label() == "Fireworks AI"
        there = agent.process_query("What is my name?")["result"]
        assert "Sam" in there

        agent.switch_model(local)
        assert agent._inference_location().remote is False
        back = agent.process_query("And what was that sum?")["result"]
        assert "42" in back


def test_a_refused_switch_leaves_the_agent_answering_on_its_old_model(
    require_lemonade,
):
    with _AddAgent(silent_mode=True) as agent:
        before = agent.model_id
        with pytest.raises(RuntimeError, match="Unknown Lemonade model"):
            agent.switch_model("no-such-model-GGUF")
        assert agent.model_id == before
        assert "42" in agent.process_query("What is 17 + 25?")["result"]


class _AnsweringConsole(SilentConsole):
    """A console whose user picks the second choice."""

    def __init__(self):
        super().__init__()
        self.asked = []

    def request_user_input_blocking(self, message, choices=None, **_):
        self.asked.append((message, choices))
        return "green"


class _AskingAgent(Agent, UserInputToolsMixin):
    def _get_system_prompt(self) -> str:
        return (
            "You are a terse assistant. When the user asks you to ask them "
            "something, call request_user_input, then report their answer."
        )

    def _register_tools(self) -> None:
        self.register_user_input_tools()


def test_the_model_asks_the_user_and_uses_the_answer(require_lemonade):
    console = _AnsweringConsole()
    with _AskingAgent(silent_mode=True, output_handler=console) as agent:
        result = agent.process_query(
            "Ask me whether I prefer blue or green, then tell me what I chose."
        )
    assert console.asked, result["result"]
    assert "green" in result["result"].lower()
