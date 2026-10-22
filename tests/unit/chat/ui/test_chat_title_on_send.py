# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A chat is named when the server takes its first message, and the
auto-titler can still improve that name, on the model the turn ran on.

A chat whose window was closed mid-answer used to stay "New Task" for good:
only the open window named it. And a cloud chat never got a written title:
the 24-token title request was spent on the model's reasoning.
"""

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import responses
from fastapi.testclient import TestClient

import gaia.ui._chat_helpers as ch
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.server import create_app

ESSAY = "Chat A: write a 1200-word essay on the history of tea."
PROVISIONAL = "Chat A: write a 1200-word essay on the history of tea"
CLOUD_MODEL = "fireworks.deepseek-v4p1-flash"
LEMONADE = "http://127.0.0.1:18767/api/v1"


@pytest.fixture
def client():
    return TestClient(create_app(db_path=":memory:"))


def _db(client) -> ChatDatabase:
    return client.app.state.db


# ── Named on send ────────────────────────────────────────────────────────


def test_first_message_names_the_chat_before_any_reply(client):
    """The window that sent it may close at once; the name must not need it."""
    sid = client.post("/api/sessions", json={"title": "New Task"}).json()["id"]
    seen = {}

    async def _client_leaves(db, session, request):
        # Runs once the server has taken the message, before any agent work.
        seen["title"] = db.get_session(session["id"])["title"]
        return
        yield  # pragma: no cover - an async generator that yields nothing

    with patch("gaia.ui.server._stream_chat_response", _client_leaves):
        client.post("/api/chat/send", json={"session_id": sid, "message": ESSAY})

    assert seen["title"] == PROVISIONAL
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["title"] == PROVISIONAL
    assert session["title_is_custom"] is False


def test_a_plain_api_turn_is_named_even_when_it_fails(client):
    sid = client.post("/api/sessions", json={}).json()["id"]
    with patch(
        "gaia.ui.server._get_chat_response", side_effect=RuntimeError("model died")
    ):
        with pytest.raises(RuntimeError):
            client.post(
                "/api/chat/send",
                json={"session_id": sid, "message": ESSAY, "stream": False},
            )
    assert client.get(f"/api/sessions/{sid}").json()["title"] == PROVISIONAL


def test_a_chosen_title_is_never_replaced(client):
    sid = client.post("/api/sessions", json={"title": "Tea research"}).json()["id"]
    with patch("gaia.ui.server._get_chat_response", return_value="ok"):
        client.post(
            "/api/chat/send",
            json={"session_id": sid, "message": ESSAY, "stream": False},
        )
    assert client.get(f"/api/sessions/{sid}").json()["title"] == "Tea research"


def test_a_later_question_does_not_rename_the_chat():
    db = ChatDatabase(":memory:")
    sid = db.create_session(title="New Task")["id"]
    ch._set_provisional_title(db, sid, ESSAY)
    ch._set_provisional_title(db, sid, "And now one about coffee?")
    assert db.get_session(sid)["title"] == PROVISIONAL


def test_a_draft_that_never_sent_keeps_the_draft_title(client):
    """The UI drops untouched "New Task" drafts; one never sent must stay one."""
    sid = client.post("/api/sessions", json={"title": "New Task"}).json()["id"]
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["title"] == "New Task" and session["message_count"] == 0


def test_an_attachment_link_is_named_by_its_file():
    assert (
        ch._title_from_question("[notes.pdf](/api/files/uploads/ab12_notes.pdf)")
        == "notes.pdf"
    )


# ── The titler replaces the provisional name ─────────────────────────────


def _retitle(db, sid, model_id, reply="History of Tea Essay"):
    gen = AsyncMock(return_value=reply)
    with (
        patch.object(ch, "_generate_session_title", gen),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
            return_value=LEMONADE,
        ),
    ):
        asyncio.run(ch._maybe_update_session_title(db, sid, ESSAY, "Tea...", model_id))
    return gen


def test_the_titler_replaces_the_provisional_name():
    db = ChatDatabase(":memory:")
    sid = db.create_session(title="New Task")["id"]
    db.begin_turn(sid, ESSAY)
    ch._set_provisional_title(db, sid, ESSAY)

    _retitle(db, sid, CLOUD_MODEL)

    session = db.get_session(sid)
    assert session["title"] == "History of Tea Essay"
    assert not session["title_is_custom"]


def test_a_rename_during_titling_wins():
    db = ChatDatabase(":memory:")
    sid = db.create_session(title="New Task")["id"]
    db.begin_turn(sid, ESSAY)
    ch._set_provisional_title(db, sid, ESSAY)

    async def _user_renames(**_kwargs):
        db.update_session(sid, title="My tea notes", title_is_custom=True)
        return "History of Tea Essay"

    with (
        patch.object(ch, "_generate_session_title", _user_renames),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
            return_value=LEMONADE,
        ),
    ):
        asyncio.run(ch._maybe_update_session_title(db, sid, ESSAY, "Tea", "m"))
    assert db.get_session(sid)["title"] == "My tea notes"


def test_a_local_guard_chat_is_not_titled_by_a_cloud_default():
    db = ChatDatabase(":memory:")
    sid = db.create_session(title="New Task")["id"]
    with patch(
        "gaia.llm.lemonade_client.resolve_default_chat_model",
        return_value=CLOUD_MODEL,
    ):
        gen = _retitle(db, sid, "Llama-Guard-3-8B-GGUF")
    gen.assert_not_awaited()


# ── The cloud title request ──────────────────────────────────────────────


@pytest.fixture
def lemonade(monkeypatch):
    """GAIA aimed at an authenticated Lemonade; every request is recorded."""
    monkeypatch.setenv("LEMONADE_BASE_URL", LEMONADE)
    monkeypatch.setenv("LEMONADE_API_KEY", "test-lemonade-key")
    with responses.RequestsMock() as http:
        yield http


def _reply(content, finish_reason="stop"):
    return {
        "id": "0",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
    }


def test_cloud_title_request_is_valid_and_leaves_room_to_reason(lemonade):
    lemonade.add(
        responses.POST,
        f"{LEMONADE}/chat/completions",
        json=_reply("History of Tea Essay"),
    )

    title = asyncio.run(
        ch._generate_session_title(LEMONADE, CLOUD_MODEL, ESSAY, "Tea began...")
    )

    assert title == "History of Tea Essay"
    # A cloud model is never checked for residency: only the completion is sent.
    (call,) = lemonade.calls
    body = json.loads(call.request.body)
    assert body["model"] == CLOUD_MODEL
    assert call.request.headers["Authorization"] == "Bearer test-lemonade-key"
    # deepseek-v4p1-flash spent all of 24 tokens reasoning (content null).
    assert body["max_completion_tokens"] >= 256
    # llama.cpp-only fields make a cloud provider answer 400.
    assert not {"id_slot", "chat_template_kwargs", "repeat_penalty"} & body.keys()


def test_a_cloud_reply_cut_short_is_logged_with_its_reason(lemonade, caplog):
    lemonade.add(
        responses.POST,
        f"{LEMONADE}/chat/completions",
        json=_reply(None, finish_reason="length"),
    )
    with caplog.at_level(logging.WARNING, logger="gaia.ui._chat_helpers"):
        title = asyncio.run(
            ch._generate_session_title(LEMONADE, CLOUD_MODEL, ESSAY, "Tea")
        )
    assert title == PROVISIONAL
    assert "finish_reason=length" in caplog.text


def test_a_failed_title_request_is_a_warning(lemonade, caplog):
    lemonade.add(
        responses.POST,
        f"{LEMONADE}/chat/completions",
        status=404,
        json={"error": "not found"},
    )
    with caplog.at_level(logging.WARNING, logger="gaia.ui._chat_helpers"):
        title = asyncio.run(
            ch._generate_session_title(LEMONADE, CLOUD_MODEL, ESSAY, "Tea")
        )
    assert title is None
    assert f"Auto-title request to {CLOUD_MODEL} failed" in caplog.text


# ── A relayed turn is titled on the model it ran on ──────────────────────


@pytest.fixture
def sidecar_flagship(tmp_path, monkeypatch):
    """The registry after a binary install of the flagship: every turn relays."""
    from gaia.agents.registry import AgentRegistry
    from gaia.hub.installer import register_installed_sidecars

    root = tmp_path / "agents"
    (root / "gaia").mkdir(parents=True)
    (root / "gaia" / ".installed").write_text(
        json.dumps(
            {
                "id": "gaia",
                "version": "0.2.0",
                "language": "python",
                "installed_at": "2026-10-22T00:00:00Z",
                "artifact_sha256": "0" * 64,
                "artifact_kind": "binary",
                "executable": "gaia-agent-gaia",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "gaia.hub.installer.default_install_root", lambda: root, raising=False
    )
    registry = AgentRegistry()
    registry._agents.clear()
    register_installed_sidecars(registry)
    monkeypatch.setattr(ch, "_agent_registry", registry)
    return registry


def test_a_relayed_cloud_turn_is_titled_by_its_cloud_model(
    monkeypatch, lemonade, sidecar_flagship
):
    relayed = {}

    def _fake_dispatch(sse_handler, request, history_pairs, model_id, agent, **kw):
        relayed["model"] = model_id
        sse_handler._emit({"type": "answer", "content": "Tea began in China."})

    monkeypatch.setattr(ch, "_dispatch_sidecar_query", _fake_dispatch)
    monkeypatch.setattr(
        "gaia.llm.lemonade_manager.LemonadeManager.get_base_url", lambda: LEMONADE
    )
    lemonade.add(
        responses.POST,
        f"{LEMONADE}/chat/completions",
        json=_reply("History of Tea Essay"),
    )

    db = ChatDatabase(":memory:")
    db.set_setting("custom_model", CLOUD_MODEL)
    session = db.create_session(title="New Task", agent_type="gaia")
    db.begin_turn(session["id"], ESSAY)
    ch._set_provisional_title(db, session["id"], ESSAY)
    request = ChatRequest(
        session_id=session["id"], message=ESSAY, stream=True, agent_type="gaia"
    )

    async def _drive():
        async for _ in ch._stream_chat_impl(
            SimpleNamespace(handler=None), db, session, request
        ):
            pass
        await asyncio.gather(*list(ch._background_tasks))

    asyncio.run(_drive())

    assert relayed["model"] == CLOUD_MODEL
    titles = [c for c in lemonade.calls if c.request.url.endswith("/chat/completions")]
    assert [json.loads(c.request.body)["model"] for c in titles] == [CLOUD_MODEL]
    assert db.get_session(session["id"])["title"] == "History of Tea Essay"
