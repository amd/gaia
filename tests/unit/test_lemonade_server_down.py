# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A stopped model server fails a turn at once and is named as the cause (#6695).

Cloud models are reached through Lemonade, so a refused connection to it is a
local failure: it used to be retried seven times over about three minutes and
then blamed on the cloud provider. A provider's own failure, relayed by a
running Lemonade, still classifies as the provider.
"""

import socket
from contextlib import nullcontext

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.base.verification import strip_verification_scope
from gaia.llm.lemonade_client import (
    LemonadeClient,
    LemonadeClientError,
    LemonadeServerDownError,
)
from gaia.llm.providers.lemonade import (
    MODEL_SERVER_DOWN,
    LemonadeNetworkError,
    LemonadeServerUnreachableError,
    classify_lemonade_exception,
)
from gaia.llm.retry import retry_state_of
from gaia.ui._chat_helpers import _done_event, _turn_failure_kind
from gaia.ui.email_sidecar.relay import _dispatch_one
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator
from tests.unit.agents.test_model_unreachable_retry import (
    _make_agent,
    _script,
    _tool_call,
)
from tests.unit.test_lemonade_retry_fake_server import (
    FakeServer,
    _json,
    _streaming_error_frame,
)

pytestmark = pytest.mark.allow_network

CLOUD_MODEL = "fireworks.glm-5"


def _closed_port() -> int:
    """A loopback port nothing listens on, so a connect is refused."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Clock:
    """Fake retry clock: advances only when the retrier sleeps."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def make_client(monkeypatch):
    for name in (
        "GAIA_LLM_RETRY_ATTEMPTS",
        "GAIA_LLM_RETRY_BASE_DELAY",
        "GAIA_LLM_RETRY_MAX_DELAY",
        "GAIA_LLM_RETRY_DEADLINE",
        "LEMONADE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    def make(port, provider="fireworks"):
        client = LemonadeClient(host="127.0.0.1", port=port, verbose=False)
        client.follows_gaia_lemonade = False
        client._ensure_model_loaded = lambda *a, **k: None
        client._model_slot_lease = lambda *a, **k: nullcontext()
        client.cloud_model_provider = lambda *a, **k: provider
        client.clock = _Clock()
        client._retry_clock = client.clock
        client._retry_sleep = client.clock.sleep
        real_probe = client._server_accepts_connections
        client.probes = []

        def probe():
            client.probes.append(1)
            return real_probe()

        client._server_accepts_connections = probe
        return client

    return make


def _chat(client, stream):
    result = client.chat_completions(
        model=CLOUD_MODEL,
        messages=[{"role": "user", "content": "hi"}],
        stream=stream,
        auto_download=False,
    )
    return list(result) if stream else result


# ── the client: fail at once, name the local server ──────────────────────


@pytest.mark.parametrize("stream", [False, True], ids=["plain", "streaming"])
def test_refused_connection_on_a_cloud_model_fails_at_once(make_client, stream):
    port = _closed_port()
    client = make_client(port)

    with pytest.raises(LemonadeServerDownError) as raised:
        _chat(client, stream)

    # One request, one quick check of the server, no backoff at all.
    assert client.last_request_retries == 0
    assert client.probes == [1]
    assert client.clock.sleeps == []
    assert client.clock.now == 0.0
    message = str(raised.value)
    assert message.startswith(
        f"The local model server at 127.0.0.1:{port} isn't answering, so the "
        "message never reached Fireworks AI."
    )
    assert "attempts failed" not in message
    assert "gaia " not in message and "`" not in message
    # Not a transient give-up: the agent loop must not wait and retry it.
    assert retry_state_of(raised.value) is None


def test_refused_connection_to_a_local_model_is_named_the_same_way(make_client):
    port = _closed_port()
    client = make_client(port, provider=None)
    with pytest.raises(LemonadeServerDownError) as raised:
        _chat(client, False)
    assert str(raised.value) == (
        f"The local model server at 127.0.0.1:{port} isn't answering. Start "
        "the model server, then send the message again."
    )


def test_a_remote_server_is_not_called_local():
    error = LemonadeServerDownError("gpu-box.lan:13305", "Fireworks AI")
    assert str(error).startswith("The model server at gpu-box.lan:13305 isn't")


def test_a_dropped_connection_to_a_live_server_is_still_retried(make_client):
    """The check connects, so the drop was momentary: the usual backoff applies."""
    import requests

    attempts = []

    def call():
        attempts.append(1)
        if len(attempts) == 1:
            raise requests.exceptions.ConnectionError("Connection reset by peer")
        return "ok"

    with FakeServer([]) as server:
        client = make_client(server.port)
        assert client._call_with_retry(call, "Chat completion", CLOUD_MODEL) == "ok"
    assert len(attempts) == 2
    assert client.probes == [1]
    assert len(client.clock.sleeps) == 1


# ── a provider failure relayed by a running Lemonade is still the provider ──


def test_relayed_provider_5xx_is_retried_and_blamed_on_the_provider(
    make_client, monkeypatch
):
    monkeypatch.setenv("GAIA_LLM_RETRY_ATTEMPTS", "2")
    script = [_json(502, {"error": {"message": "Bad Gateway"}})] * 2
    with FakeServer(script) as server:
        client = make_client(server.port)
        with pytest.raises(LemonadeClientError) as raised:
            _chat(client, False)
    assert not isinstance(raised.value, LemonadeServerDownError)
    assert len(server.requests) == 2
    assert client.probes == []
    assert "HTTP 502" in str(raised.value)
    assert "isn't answering" not in str(raised.value)
    assert not isinstance(
        classify_lemonade_exception(raised.value), LemonadeServerUnreachableError
    )


def test_relayed_provider_timeout_is_blamed_on_the_provider(make_client, monkeypatch):
    monkeypatch.setenv("GAIA_LLM_RETRY_ATTEMPTS", "2")
    frame = _streaming_error_frame("SSE stream failed: CURL error: Timeout was reached")
    with FakeServer([frame, frame]) as server:
        client = make_client(server.port)
        with pytest.raises(LemonadeClientError, match="did not answer within") as e:
            _chat(client, True)
    assert not isinstance(e.value, LemonadeServerDownError)
    assert len(server.requests) == 2
    assert client.probes == []


# ── classification ───────────────────────────────────────────────────────


def test_classifies_the_typed_error_through_a_wrapper():
    down = LemonadeServerDownError("127.0.0.1:13305", "Fireworks AI")
    try:
        raise RuntimeError("Error in send_messages") from down
    except RuntimeError as wrapped:
        classified = classify_lemonade_exception(wrapped)
    assert isinstance(classified, LemonadeServerUnreachableError)
    assert isinstance(classified, LemonadeNetworkError)
    assert classified.retryable is False
    assert classified.endpoint == "127.0.0.1:13305"
    assert classified.user_message == str(down)


def test_classifies_the_flattened_wording():
    flat = Exception(
        "Streaming failed: The local model server at 127.0.0.1:13305 isn't "
        "answering, so the message never reached Fireworks AI. Start the model "
        "server, then send the message again."
    )
    classified = classify_lemonade_exception(flat)
    assert isinstance(classified, LemonadeServerUnreachableError)
    assert classified.endpoint == "127.0.0.1:13305"
    assert classified.user_message.startswith("The local model server at")


# ── the agent: one failed call, no step retries, the server named ────────


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


@pytest.mark.parametrize("streaming", [False, True], ids=["non-streaming", "streaming"])
def test_agent_names_the_local_server_not_the_provider(clean_registry, streaming):
    agent = _make_agent(streaming)
    agent._model_service_label = lambda: "Fireworks AI"
    down = LemonadeServerDownError("127.0.0.1:13305", "Fireworks AI")
    sent = _script(agent, _tool_call(1), down)

    result = agent.process_query("Check the ledger", max_steps=10)

    text = strip_verification_scope(result["result"])
    # Asked once after the tool call: no step retries, no cool-down.
    assert len(sent) == 2
    assert agent.sleeps == []
    assert result["model_retries"]["step_retries"] == 0
    assert "Fireworks AI: " not in text
    assert "The local model server at 127.0.0.1:13305 isn't answering" in text
    assert "Start the model server, then send the message again." in text
    assert "attempts failed" not in text
    assert result["model_failed"] is True
    assert result["model_server_down"] is True
    assert result["status"] == "failed"


def test_agent_reports_other_failures_without_the_server_down_flag(clean_registry):
    agent = _make_agent(False)
    _script(agent, LemonadeClientError("Cloud rate limit reached (HTTP 429)."))
    result = agent.process_query("hi", max_steps=3)
    assert result["model_failed"] is True
    assert result["model_server_down"] is False


# ── the cause reaches the Agent UI ───────────────────────────────────────


def test_the_answer_event_carries_the_cause():
    handler = SSEOutputHandler()
    handler.mark_turn_failed(failure=MODEL_SERVER_DOWN)
    handler.print_final_answer("The model call failed.")
    event = handler.event_queue.get_nowait()
    assert (event["failed"], event["failure"]) == (True, MODEL_SERVER_DOWN)

    handler.mark_turn_failed()
    handler.print_final_answer("Another failure.")
    event = handler.event_queue.get_nowait()
    assert event["failed"] is True and "failure" not in event


def test_the_cause_survives_the_sidecar_hop():
    translator = CanonicalTranslator(run_id="run-1", agent_id="gaia")
    (final,) = translator.translate(
        {
            "type": "answer",
            "content": "The model call failed.",
            "failed": True,
            "failure": MODEL_SERVER_DOWN,
        }
    )
    assert final["failure"] == MODEL_SERVER_DOWN

    handler = SSEOutputHandler()
    _dispatch_one(handler, final)
    answer = handler.event_queue.get_nowait()
    assert (answer["type"], answer["failed"], answer["failure"]) == (
        "answer",
        True,
        MODEL_SERVER_DOWN,
    )


def test_the_done_event_names_the_cause():
    answer = {"type": "answer", "failed": True, "failure": MODEL_SERVER_DOWN}
    kind = _turn_failure_kind(answer, None)
    done = _done_event(7, "text", False, failed=True, failure=kind)
    assert done["failure"] == MODEL_SERVER_DOWN
    assert _turn_failure_kind(None, {"model_server_down": True}) == MODEL_SERVER_DOWN
    assert _turn_failure_kind({"failed": True}, {"model_failed": True}) is None
    assert "failure" not in _done_event(7, "text", False, failed=True)
