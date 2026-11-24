# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A turn fails at once when the model server the user chose refuses connections.

GAIA starts only its own server. With ``LEMONADE_BASE_URL`` at a server that is
down, a message used to wait about a minute and then say the model "may be
loading or briefly busy -- try sending the message again" (#8343).
"""

import socket

import pytest

from gaia.llm.lemonade_client import LemonadeServerDownError
from gaia.llm.providers.lemonade import (
    LemonadeServerUnreachableError,
    classify_lemonade_exception,
)
from gaia.ui import _chat_helpers as helpers

pytestmark = pytest.mark.allow_network

# Unit tests run with the probe stubbed out; these put the real one back.
_REAL_PROBE = helpers._stopped_users_server


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _real_probe_and_no_eval_provider(monkeypatch):
    monkeypatch.setattr(helpers, "_stopped_users_server", _REAL_PROBE)
    monkeypatch.delenv(helpers._EVAL_PROVIDER_ENV, raising=False)


def test_a_refusing_server_the_user_chose_fails_the_turn(monkeypatch):
    port = _closed_port()
    monkeypatch.setenv("LEMONADE_BASE_URL", f"http://127.0.0.1:{port}/api/v1")

    with pytest.raises(LemonadeServerDownError) as caught:
        helpers._refuse_turn_on_stopped_users_server()

    message = str(caught.value)
    assert f"127.0.0.1:{port}" in message and "isn't answering" in message
    for wrong in ("briefly busy", "may be loading"):
        assert wrong not in message
    classified = classify_lemonade_exception(caught.value)
    assert isinstance(classified, LemonadeServerUnreachableError)
    assert classified.retryable is False


def test_a_listening_server_the_user_chose_lets_the_turn_go_on(monkeypatch):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        monkeypatch.setenv("LEMONADE_BASE_URL", f"http://127.0.0.1:{port}/api/v1")

        helpers._refuse_turn_on_stopped_users_server()


def test_gaias_own_server_is_never_probed_here(monkeypatch):
    """Its restart is the pre-flight's job; a stopped one must not fail the turn."""
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.configured_lemonade_url", lambda: None
    )

    def no_probe(self):
        raise AssertionError("GAIA's own server was probed")

    monkeypatch.setattr(
        "gaia.llm.lemonade_client.LemonadeClient._server_accepts_connections",
        no_probe,
    )
    helpers._refuse_turn_on_stopped_users_server()


def test_a_turn_on_another_provider_is_not_held_to_lemonade(monkeypatch):
    monkeypatch.setenv(helpers._EVAL_PROVIDER_ENV, "claude")
    monkeypatch.setenv("GAIA_EVAL_CLAUDE_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("LEMONADE_BASE_URL", f"http://127.0.0.1:{_closed_port()}/api/v1")
    helpers._refuse_turn_on_stopped_users_server()


@pytest.mark.asyncio
async def test_a_streamed_turn_ends_at_once_with_the_server_named(monkeypatch):
    import json
    from unittest.mock import AsyncMock, patch

    from gaia.ui.database import ChatDatabase
    from gaia.ui.models import ChatRequest
    from gaia.ui.run_manager import RunManager

    monkeypatch.setattr(helpers, "_stopped_users_server", lambda: "127.0.0.1:59999")
    # CI has no hub agents installed; the server check comes after this one.
    monkeypatch.setattr(helpers, "_agent_type_unknown", lambda *a: False)
    built = []
    monkeypatch.setattr(
        helpers, "_get_cached_agent", lambda *a, **k: built.append(a) or None
    )
    db = ChatDatabase(":memory:")
    events = []
    try:
        with (
            patch("gaia.ui.run_manager.run_manager", RunManager()),
            patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
        ):
            session = db.create_session(model="M-GGUF", agent_type="chat")
            request = ChatRequest(session_id=session["id"], message="hi", stream=True)
            db.begin_turn(session["id"], request.message)
            async for chunk in helpers._stream_chat_response(db, session, request):
                for line in chunk.splitlines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[len("data: ") :]))
    finally:
        db.close()

    said = " ".join(str(e.get("content") or "") for e in events)
    assert "127.0.0.1:59999 isn't answering" in said, events
    assert "briefly busy" not in said
    # Nothing was built or probed on the way.
    assert built == []


@pytest.mark.asyncio
async def test_a_non_streamed_turn_ends_at_once_with_the_same_words(monkeypatch):
    from gaia.ui.database import ChatDatabase
    from gaia.ui.models import ChatRequest

    monkeypatch.setattr(helpers, "_stopped_users_server", lambda: "127.0.0.1:59999")
    monkeypatch.setattr(helpers, "_agent_type_unknown", lambda *a: False)
    waited, built = [], []
    monkeypatch.setattr(helpers, "wait_for_warm_up", lambda **k: waited.append(k))
    monkeypatch.setattr(
        helpers, "_get_cached_agent", lambda *a, **k: built.append(a) or None
    )
    db = ChatDatabase(":memory:")
    turn_box: dict = {}
    try:
        session = db.create_session(model="M-GGUF", agent_type="chat")
        request = ChatRequest(session_id=session["id"], message="hi", stream=False)
        said = await helpers._get_chat_response(db, session, request, turn_box=turn_box)
    finally:
        db.close()

    expected = str(LemonadeServerDownError("127.0.0.1:59999"))
    assert said.split("\n\n")[0] == helpers._agent_ui_wording(expected)
    assert "briefly busy" not in said
    assert turn_box["failed"] is True
    assert waited == [] and built == []
