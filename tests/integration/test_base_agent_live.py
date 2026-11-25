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
from gaia.agents.base.tools import _TOOL_REGISTRY, tool

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
