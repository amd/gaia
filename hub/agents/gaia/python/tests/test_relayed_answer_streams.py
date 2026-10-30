# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A relayed flagship answer reaches the Agent UI while it is still being written.

The whole hop runs for real: the base agent loop with a fake model that writes
its reply in pieces, the sidecar's ``/query`` route on a real socket, and the
Agent UI's ``relay_query`` reading it through ``SidecarProxy``. The model parks
part-way through its reply, so a chunk can only reach the UI before the turn
ends if every hop streams.

The sidecar used to build its agent with streaming off, so a relayed turn sat
on "Waiting on the model" until the model finished and then showed the whole
answer at once, and Stop left an empty bubble.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("gaia_agent")

import uvicorn  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402

from gaia.agents.base.agent import Agent  # noqa: E402
from gaia.ui.email_sidecar.profiles import GAIA_PROFILE  # noqa: E402
from gaia.ui.email_sidecar.proxy import SidecarProxy  # noqa: E402
from gaia.ui.email_sidecar.relay import relay_query  # noqa: E402
from gaia.ui.sse_handler import SSEOutputHandler  # noqa: E402

_HEAD = "Once upon a time, "
_TAIL = "a fox learned to read."
#: How long the fake model may park before the test gives up on it.
_PARK_S = 15.0
#: How long the UI may wait for the first chunk of a parked reply.
_FIRST_CHUNK_S = 8.0


class _LoopAgent(Agent):
    """The real base loop, no tools — the part that decides whether to stream."""

    AUTOLOAD_DECLARED_SKILLS = False

    def close(self) -> None:
        pass

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass


class _Model:
    """A model that writes the opening of its reply, then parks until released.

    Answers in the ``{"thought", "answer"}`` envelope, as a gateway model the
    catalog doesn't mark as tool-calling does.
    """

    def __init__(self):
        self.release = threading.Event()

    def _pieces(self):
        return ['{"thought": "write it", "answer": "', _HEAD], [_TAIL, '"}']

    def send_messages_stream(self, **_kwargs):
        head, tail = self._pieces()
        for text in head:
            yield SimpleNamespace(text=text, is_complete=False, stats={})
        self.release.wait(_PARK_S)
        for text in tail:
            yield SimpleNamespace(text=text, is_complete=False, stats={})
        yield SimpleNamespace(text="", is_complete=True, stats={})

    def send_messages(self, **_kwargs):
        # An unstreamed call returns nothing until the whole reply is written.
        self.release.wait(_PARK_S)
        head, tail = self._pieces()
        return SimpleNamespace(text="".join(head + tail), stats={})

    def get_stats(self):
        return {}


class _UIHandler(SSEOutputHandler):
    """The Agent UI's handler, recording when each event reached it."""

    def __init__(self):
        super().__init__()
        self.seen = []
        self.first_chunk = threading.Event()

    def _emit(self, event):
        self.seen.append(event)
        if event.get("type") == "chunk":
            self.first_chunk.set()
        super()._emit(event)


@pytest.fixture
def sidecar(monkeypatch):
    """A real sidecar on an ephemeral port, building the real base loop."""
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    monkeypatch.setattr(server_mod, "_warmup_blocking", lambda: None)

    models: list = []

    def build(**_kw):
        # Built the way the sidecar builds GaiaAgent: config default, no stream.
        with patch("gaia.agents.base.agent.AgentSDK"):
            agent = _LoopAgent(silent_mode=True, skip_lemonade=True)
        model = _Model()
        agent.chat = model
        models.append(model)
        return agent

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)
    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()

    from gaia.daemon.sidecars.manager import find_free_port

    port = find_free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            server_mod.build_app(), host="127.0.0.1", port=port, log_level="error"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, f"test sidecar never started on port {port}"
    proxy = SidecarProxy(f"http://127.0.0.1:{port}", agent_id="gaia", timeout=30)
    try:
        yield proxy, models
    finally:
        for model in models:
            model.release.set()
        server.should_exit = True
        thread.join(10)
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


def _relay_in_background(proxy, handler, session_id):
    def run():
        relay_query(
            handler,
            proxy,
            query="Write a story",
            context=[],
            profile=GAIA_PROFILE,
            session_id=session_id,
            read_timeout=30,
        )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _streamed_text(handler):
    return "".join(e.get("content", "") for e in handler.seen if e["type"] == "chunk")


@pytest.mark.parametrize("session_id", [None, "s-relay-stream"])
def test_answer_text_reaches_the_ui_before_the_model_finishes(sidecar, session_id):
    proxy, models = sidecar
    handler = _UIHandler()

    relay = _relay_in_background(proxy, handler, session_id)

    assert handler.first_chunk.wait(_FIRST_CHUNK_S), (
        "no answer text reached the UI while the model was still writing; "
        f"events so far: {handler.seen}"
    )
    assert not models[0].release.is_set()
    assert relay.is_alive(), "the turn ended before the model finished"
    assert _streamed_text(handler) == _HEAD
    assert "thought" not in _streamed_text(handler)

    models[0].release.set()
    relay.join(15)
    assert not relay.is_alive()
    assert _streamed_text(handler) == _HEAD + _TAIL
    answers = [e for e in handler.seen if e["type"] == "answer"]
    assert [a["content"] for a in answers] == [_HEAD + _TAIL], handler.seen


def test_stop_keeps_the_text_written_so_far(sidecar):
    """Stop mid-answer: the UI keeps the opening it already showed."""
    proxy, models = sidecar
    handler = _UIHandler()

    relay = _relay_in_background(proxy, handler, "s-relay-stop")
    assert handler.first_chunk.wait(_FIRST_CHUNK_S), handler.seen

    # What POST /api/chat/cancel does to a relayed turn.
    handler.cancelled.set()
    response = handler.active_relay_response
    if response is not None:
        response.close()
    relay.join(15)
    assert not relay.is_alive()

    assert _streamed_text(handler) == _HEAD
    assert not [e for e in handler.seen if e["type"] in ("answer", "agent_error")]
