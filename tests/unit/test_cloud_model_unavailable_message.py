# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A cloud model the provider answers 404 for gets a "not available" error.

Measured in the Agent UI on rc13 with ``amd.No-Such-Model-404``: the reply
said "This might be a temporary issue — try again in a moment", then sent the
user to "the TUI provider settings" from inside the Agent UI.
"""

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import gaia.ui._chat_helpers as helpers
from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.llm.lemonade_client import (
    CloudModelUnavailableError,
    LemonadeAuthError,
    _cloud_request_error,
)
from gaia.llm.providers.lemonade import (
    LemonadeCloudAccountError,
    LemonadeCloudModelUnavailableError,
    classify_lemonade_exception,
)
from gaia.llm.surface_hints import AGENT_UI_MODEL_SWITCH_HINT, TUI_MODEL_SWITCH_HINT
from gaia.ui._chat_helpers import _agent_ui_wording, _classify_chat_exception
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest

MODEL = "amd.No-Such-Model-404"


def _wrapped(error: BaseException) -> RuntimeError:
    """How AgentSDK hands a client error to the agent loop."""
    try:
        raise error
    except Exception as e:
        try:
            raise RuntimeError(f"Error in send_messages: {e}") from e
        except RuntimeError as wrapped:
            return wrapped


def test_a_404_names_the_model_and_provider_and_is_not_retryable():
    err = classify_lemonade_exception(
        _wrapped(_cloud_request_error(404, "amd", model=MODEL))
    )
    assert isinstance(err, LemonadeCloudModelUnavailableError)
    assert err.retryable is False
    assert err.model_id == MODEL
    msg = err.user_message
    assert f"`{MODEL}` isn't available on the AMD LLM Gateway (HTTP 404)" in msg
    assert "temporary" not in msg and "try again in a moment" not in msg
    assert msg.endswith(TUI_MODEL_SWITCH_HINT)


def test_a_404_flattened_to_text_is_still_recognised():
    """A wrapper that drops the cause chain keeps only the client's wording."""
    flat = RuntimeError(f"Error in send_messages: {_cloud_request_error(404, 'amd')}")
    assert flat.__cause__ is None
    err = _classify_chat_exception(flat)
    assert isinstance(err, LemonadeCloudModelUnavailableError)
    assert err.user_message.startswith("The selected cloud model isn't available")


def test_an_unknown_provider_still_reads_as_a_sentence():
    err = LemonadeCloudModelUnavailableError(MODEL)
    assert "isn't available on its cloud provider (HTTP 404)" in err.user_message


@pytest.mark.parametrize("status", [401, 403, 429, 500, None])
def test_other_cloud_failures_are_not_called_unavailable(status):
    err = classify_lemonade_exception(
        _wrapped(_cloud_request_error(status, "amd", model=MODEL))
    )
    assert not isinstance(err, LemonadeCloudModelUnavailableError)


def test_a_local_404_for_an_uninstalled_model_is_not_a_cloud_404():
    err = classify_lemonade_exception(
        RuntimeError("Lemonade: model 'Qwen3-0.6B-GGUF' was not found (HTTP 404)")
    )
    assert not isinstance(err, LemonadeCloudModelUnavailableError)


def test_auth_error_in_the_chain_wins_over_wording():
    """A 401 whose text happens to quote the 404 wording stays an auth error."""
    err = classify_lemonade_exception(
        LemonadeAuthError("The selected cloud model is unavailable (HTTP 404).")
    )
    assert not isinstance(err, LemonadeCloudModelUnavailableError)


def test_the_typed_error_survives_a_cause_cycle():
    a = CloudModelUnavailableError("x", model_id=MODEL, provider="amd")
    b = RuntimeError("wrapper")
    a.__cause__, b.__cause__ = b, a
    err = classify_lemonade_exception(b)
    assert isinstance(err, LemonadeCloudModelUnavailableError)


# --- the Agent UI says how to switch in the Agent UI -------------------------


def test_agent_ui_wording_replaces_only_the_tui_hint():
    msg = LemonadeCloudModelUnavailableError(MODEL, "amd").user_message
    shown = _agent_ui_wording(msg)
    assert "TUI" not in shown and "/model" not in shown
    assert shown.endswith(AGENT_UI_MODEL_SWITCH_HINT)
    assert shown.startswith(f"`{MODEL}` isn't available on the AMD LLM Gateway")
    assert _agent_ui_wording(shown) == shown


@pytest.mark.parametrize("text", ["", "All done.", "Use /model in the TUI to pick"])
def test_agent_ui_wording_leaves_other_text_alone(text):
    assert _agent_ui_wording(text) == text


def test_tui_and_agent_ui_hints_differ_and_name_their_own_surface():
    assert "/model" in TUI_MODEL_SWITCH_HINT
    assert "TUI" not in AGENT_UI_MODEL_SWITCH_HINT
    assert "model menu" in AGENT_UI_MODEL_SWITCH_HINT
    assert "Settings" in AGENT_UI_MODEL_SWITCH_HINT


# --- the agent loop ends the turn with it -------------------------------------


class _PlainAgent(Agent):
    def _get_system_prompt(self):
        return "Answer."

    def _register_tools(self):
        return None


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _PlainAgent(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    agent.console = MagicMock()
    agent.console.cancelled = None
    yield agent
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def test_the_turn_ends_with_the_unavailable_message_not_try_again(agent):
    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = _wrapped(
        _cloud_request_error(404, "amd", model=MODEL)
    )
    result = agent.process_query("Hello, are you there?")
    answer = result["result"]
    assert f"`{MODEL}` isn't available on the AMD LLM Gateway" in answer
    assert "temporary" not in answer
    assert "Technical details" not in answer
    assert agent.chat.send_messages.call_count == 1, "a 404 must not be retried"
    errors = [c.args[0] for c in agent.console.print_error.call_args_list]
    assert any(MODEL in e for e in errors), errors


def test_a_refused_account_is_still_reported_as_billing(agent):
    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = _wrapped(
        _cloud_request_error(412, "fireworks", model=MODEL)
    )
    result = agent.process_query("Hello")
    assert "Fireworks AI refused the request" in result["result"]
    assert isinstance(
        classify_lemonade_exception(_wrapped(_cloud_request_error(412))),
        LemonadeCloudAccountError,
    )


# --- end to end through the Agent UI chat path --------------------------------


class _UnavailableAgent:
    """Cached-agent stand-in whose turn ends the way the real loop does."""

    def __init__(self):
        self.indexed_files = set()
        self.conversation_history = []
        self.rag = None
        self.path_validator = None
        self._cancel_event = threading.Event()
        self.console = None

    def _register_tools(self):
        pass

    def process_query(self, _message, **_kwargs):
        msg = LemonadeCloudModelUnavailableError(MODEL, "amd").user_message
        if self.console is not None:
            self.console.print_error(msg)
            self.console.print_final_answer(msg)
        return {"result": msg, "status": "failed"}


@pytest.fixture
def ui_chat(monkeypatch):
    monkeypatch.setattr(helpers, "_agent_registry", None)
    monkeypatch.setattr(helpers, "_maybe_load_expected_model", lambda *a, **k: None)
    helpers._agent_cache.clear()
    db = ChatDatabase(":memory:")
    session = db.create_session(model="M-GGUF", agent_type="chat")
    helpers._store_agent(session["id"], "M-GGUF", [], _UnavailableAgent(), "chat")
    yield db, db.get_session(session["id"])
    helpers._agent_cache.clear()
    db.close()


def test_the_agent_ui_stream_shows_agent_ui_advice(ui_chat):
    db, session = ui_chat
    request = ChatRequest(session_id=session["id"], message="Hello", stream=True)
    events = []

    async def drain():
        async for chunk in helpers._stream_chat_impl(
            SimpleNamespace(handler=None), db, session, request
        ):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

    asyncio.run(drain())
    shown = [
        e["content"]
        for e in events
        if e.get("type") in ("answer", "agent_error", "done") and e.get("content")
    ]
    assert shown, events
    for text in shown:
        assert TUI_MODEL_SWITCH_HINT not in text
    assert any(AGENT_UI_MODEL_SWITCH_HINT in t for t in shown)
    stored = db.get_messages(session["id"])[-1]["content"]
    assert AGENT_UI_MODEL_SWITCH_HINT in stored and "TUI" not in stored


def test_the_agent_ui_non_streaming_reply_shows_agent_ui_advice(ui_chat):
    db, session = ui_chat
    request = ChatRequest(session_id=session["id"], message="Hello", stream=False)
    reply = asyncio.run(helpers._get_chat_response(db, session, request))
    assert AGENT_UI_MODEL_SWITCH_HINT in reply
    assert TUI_MODEL_SWITCH_HINT not in reply
