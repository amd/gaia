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


def test_attachments_are_left_out_of_a_question_title():
    """It read "hi !red.png notes.txt" with an image and a file attached."""
    sent = (
        "hi\n\n![red.png](/api/files/uploads/ab12_red.png) "
        "[notes.txt](/api/files/uploads/cd34_notes.txt)"
    )
    assert ch._title_from_question(sent) == "hi"


def test_an_image_sent_alone_is_named_by_its_file():
    assert ch._title_from_question("![red.png](/api/files/uploads/ab12_red.png)") == (
        "red.png"
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


def _provisional_chat():
    db = ChatDatabase(":memory:")
    sid = db.create_session(title="New Task")["id"]
    db.begin_turn(sid, ESSAY)
    ch._set_provisional_title(db, sid, ESSAY)
    return db, sid


def test_a_failed_title_is_requested_again_next_turn():
    db, sid = _provisional_chat()

    _retitle(db, sid, CLOUD_MODEL, reply=None)
    retry = _retitle(db, sid, CLOUD_MODEL)

    retry.assert_awaited_once()
    assert db.get_session(sid)["title"] == "History of Tea Essay"


def test_a_title_that_keeps_failing_stops_being_requested():
    """Each retry would be another billed call on a cloud model."""
    db, sid = _provisional_chat()

    tries = [_retitle(db, sid, CLOUD_MODEL, reply=None) for _ in range(4)]

    assert [t.await_count for t in tries] == [1, 1, 1, 0]
    assert db.get_session(sid)["title"] == PROVISIONAL


def test_small_talk_stops_being_titled_after_a_while():
    """A chat of greetings would otherwise bill a cloud title every turn."""
    db, sid = _provisional_chat()

    def _no_topic():
        gen = AsyncMock(side_effect=ch._NoTopicYet)
        with (
            patch.object(ch, "_generate_session_title", gen),
            patch(
                "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
                return_value=LEMONADE,
            ),
        ):
            asyncio.run(ch._maybe_update_session_title(db, sid, ESSAY, "", CLOUD_MODEL))
        return gen.await_count

    counts = [_no_topic() for _ in range(ch._AUTO_TITLE_MAX_ATTEMPTS + 1)]

    assert counts[:-1] == [1] * ch._AUTO_TITLE_MAX_ATTEMPTS and counts[-1] == 0
    assert db.get_session(sid)["title"] == PROVISIONAL


# ── Every title request counts; a title the chat already has is final ────


def _attempt(db, sid, outcome, model_id=CLOUD_MODEL, at_turn_start=False):
    """One title try whose outcome is *outcome*; returns how many were made."""
    ask = AsyncMock(side_effect=outcome)
    with (
        patch.object(ch, "_title_when_model_free", ask),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
            return_value=LEMONADE,
        ),
    ):
        try:
            asyncio.run(
                ch._maybe_update_session_title(
                    db, sid, ESSAY, "Tea", model_id, at_turn_start=at_turn_start
                )
            )
        except asyncio.CancelledError:
            pass
    return ask.await_count


@pytest.mark.parametrize(
    "reply",
    [
        PROVISIONAL,
        PROVISIONAL.upper(),
        PROVISIONAL.title(),
        f'"{PROVISIONAL}."',
        f"  {PROVISIONAL.replace(' ', '   ')}!",
        f"`{PROVISIONAL}`",
    ],
)
def test_a_reply_naming_the_chat_as_it_is_ends_its_titling(reply):
    """The chat asked again at the start and end of every turn, billing a cloud
    title each time: the same name was neither saved nor counted."""
    db, sid = _provisional_chat()

    calls = [
        _attempt(db, sid, [reply], at_turn_start=start)
        for _ in range(4)
        for start in (True, False)
    ]

    assert calls == [1] + [0] * 7
    assert db.get_session(sid)["title"] == PROVISIONAL


def test_a_short_question_titled_back_in_title_case_is_asked_once(lemonade):
    """ "Python list comprehension" came back "Python List Comprehension"."""
    question = "Python list comprehension"
    lemonade.add(
        responses.POST,
        f"{LEMONADE}/chat/completions",
        json=_reply("Python List Comprehension"),
    )
    db = ChatDatabase(":memory:")
    sid = db.create_session(title="New Task")["id"]
    db.begin_turn(sid, question)
    ch._set_provisional_title(db, sid, question)

    with patch(
        "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
        return_value=LEMONADE,
    ):
        for _ in range(3):
            for start in (True, False):
                asyncio.run(
                    ch._maybe_update_session_title(
                        db, sid, question, "Use [x for x in y]", CLOUD_MODEL, start
                    )
                )

    assert len(lemonade.calls) == 1
    assert db.get_session(sid)["title"] == question


@pytest.mark.parametrize(
    "reply",
    [
        f"{PROVISIONAL}s",
        f"{PROVISIONAL} and coffee",
        "History of Tea Essay",
    ],
)
def test_a_title_that_differs_by_a_word_is_still_written(reply):
    db, sid = _provisional_chat()

    assert _attempt(db, sid, [reply]) == 1

    assert db.get_session(sid)["title"] == reply
    # Named now: later turns leave it alone.
    assert _attempt(db, sid, ["Something else"]) == 0


@pytest.mark.parametrize(
    "outcome",
    [
        ch._NoTopicYet,
        ch._TitleModelBusy("not granted within its 30s wait limit"),
        asyncio.CancelledError,
    ],
    ids=["no-topic", "model-busy", "cancelled"],
)
def test_every_kind_of_title_try_counts_toward_the_cap(outcome):
    """A model that stayed busy was asked again every turn, without end."""
    db, sid = _provisional_chat()

    calls = [_attempt(db, sid, outcome) for _ in range(ch._AUTO_TITLE_MAX_ATTEMPTS + 2)]

    assert calls == [1] * ch._AUTO_TITLE_MAX_ATTEMPTS + [0, 0]
    assert sid not in ch._AUTO_TITLE_RUNNING


def test_mixed_title_outcomes_share_one_cap():
    db, sid = _provisional_chat()
    busy = ch._TitleModelBusy("busy")
    # Two failures, so the failure cap of 3 never ends it first.
    outcomes = [busy, ch._NoTopicYet, [None]] * 2 + [busy, ch._NoTopicYet] * 3

    calls = [_attempt(db, sid, outcome) for outcome in outcomes]

    assert calls == [1] * ch._AUTO_TITLE_MAX_ATTEMPTS + [0, 0]


def test_the_title_write_never_overwrites_a_rename():
    db = ChatDatabase(":memory:")
    sid = db.create_session(title="New Task")["id"]
    ch._set_provisional_title(db, sid, ESSAY)

    db.update_session(sid, title="My tea notes")
    assert not db.replace_auto_title(sid, PROVISIONAL, "History of Tea Essay")
    assert db.get_session(sid)["title"] == "My tea notes"

    db.update_session(sid, title=PROVISIONAL, title_is_custom=True)
    assert not db.replace_auto_title(sid, PROVISIONAL, "History of Tea Essay")
    assert db.get_session(sid)["title"] == PROVISIONAL


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
    # A failed try, so the next turn asks again instead of keeping the question.
    assert title is None
    assert "finish_reason=length" in caplog.text


def test_a_greeting_waits_for_the_question_that_names_the_chat(lemonade):
    """Gemma titled "hi" as "User Greeting Input", and the chat kept that name
    after the user asked about Lisbon."""
    lisbon = "Can you help me plan a weekend trip to Lisbon?"
    for reply in ("none", "Plan Lisbon weekend trip"):
        lemonade.add(responses.POST, f"{LEMONADE}/chat/completions", json=_reply(reply))
    db = ChatDatabase(":memory:")
    sid = db.create_session(title="New Task")["id"]
    db.begin_turn(sid, "hi")
    ch._set_provisional_title(db, sid, "hi")

    def _turn(question, answer):
        with patch(
            "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
            return_value=LEMONADE,
        ):
            asyncio.run(
                ch._maybe_update_session_title(db, sid, question, answer, CLOUD_MODEL)
            )
        return db.get_session(sid)["title"]

    assert _turn("hi", "Hey! What can I do?") == "hi"
    db.add_message(sid, "assistant", "Hey! What can I do?", ends_turn=True)
    db.begin_turn(sid, lisbon)
    assert _turn(lisbon, "Sure, here is a plan") == "Plan Lisbon weekend trip"
    prompt = json.loads(lemonade.calls[1].request.body)["messages"][0]["content"]
    assert "User: hi" in prompt and lisbon in prompt


@pytest.mark.parametrize(
    "reply", ["none", "None", "NONE", "none.", '"none"', "Title: none", "none\nTitle"]
)
def test_a_none_reply_means_no_topic_yet(lemonade, reply):
    lemonade.add(responses.POST, f"{LEMONADE}/chat/completions", json=_reply(reply))
    with pytest.raises(ch._NoTopicYet):
        asyncio.run(ch._generate_session_title(LEMONADE, CLOUD_MODEL, "hi", ""))


@pytest.mark.parametrize(
    "reply",
    [
        "None values in pandas",
        "None vs null in JavaScript",
        "Nonexistent file error",
        "Handling none and empty strings",
    ],
)
def test_a_real_title_starting_with_none_is_kept(lemonade, reply):
    lemonade.add(responses.POST, f"{LEMONADE}/chat/completions", json=_reply(reply))
    title = asyncio.run(
        ch._generate_session_title(LEMONADE, CLOUD_MODEL, "a pandas question", "")
    )
    assert title == reply


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


# ── A local title request ────────────────────────────────────────────────

LOCAL_MODEL = "Gemma-4-E4B-it-GGUF"


def _health(slots):
    args = f"--parallel {slots} --kv-unified" if slots > 1 else ""
    return {
        "status": "ok",
        "all_models_loaded": [
            {"model_name": LOCAL_MODEL, "recipe_options": {"llamacpp_args": args}}
        ],
    }


@pytest.fixture
def local_lemonade(monkeypatch):
    monkeypatch.setenv("LEMONADE_BASE_URL", LEMONADE)
    monkeypatch.setenv("LEMONADE_API_KEY", "test-lemonade-key")
    monkeypatch.setattr(
        "gaia.llm.lemonade_manager.LemonadeManager.get_base_url", lambda: LEMONADE
    )
    with responses.RequestsMock(assert_all_requests_are_fired=False) as http:
        yield http


def _title_calls(http):
    return [
        json.loads(c.request.body)
        for c in http.calls
        if c.request.url.endswith("/chat/completions")
    ]


def test_a_local_title_request_turns_thinking_off(local_lemonade):
    """Gemma reasoned through all 24 tokens on a short question and titled nothing."""
    local_lemonade.add(responses.GET, f"{LEMONADE}/health", json=_health(2))
    local_lemonade.add(
        responses.POST, f"{LEMONADE}/chat/completions", json=_reply("Greeting")
    )

    title = asyncio.run(ch._generate_session_title(LEMONADE, LOCAL_MODEL, "hi", ""))

    assert title == "Greeting"
    (body,) = _title_calls(local_lemonade)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["id_slot"] == 1


def test_a_busy_model_is_waited_out(local_lemonade, monkeypatch):
    """Another chat held the model past the wait limit; the title came later."""
    from contextlib import contextmanager

    from gaia.daemon.broker_client import BrokerUnavailableError

    waits = []

    @contextmanager
    def _busy_once(model, **_kw):
        waits.append(model)
        if len(waits) == 1:
            raise BrokerUnavailableError(
                f"no model-slot lease for '{model}': not granted within its "
                "30s wait limit",
                status_code=504,
            )
        yield None

    monkeypatch.setattr("gaia.daemon.broker_client.model_lease", _busy_once)
    monkeypatch.setattr(ch, "_TITLE_BUSY_RETRY_S", 0, raising=False)
    local_lemonade.add(responses.GET, f"{LEMONADE}/health", json=_health(2))
    local_lemonade.add(
        responses.POST,
        f"{LEMONADE}/chat/completions",
        json=_reply("History of Tea Essay"),
    )
    db, sid = _provisional_chat()

    asyncio.run(ch._maybe_update_session_title(db, sid, ESSAY, "Tea", LOCAL_MODEL))

    assert db.get_session(sid)["title"] == "History of Tea Essay"


# ── Titled when the turn starts ──────────────────────────────────────────


def _relayed_turn(monkeypatch, http, model, slots=None):
    """Run one relayed turn on *model*.

    Returns what the chat looked like once its turn-start title task had
    finished but before the answer was sent, and its title after the turn.
    """
    import threading

    turn_start_done = threading.Event()
    real_retitle = ch._maybe_update_session_title

    async def _retitle(*args, at_turn_start=False, **kwargs):
        try:
            await real_retitle(*args, at_turn_start=at_turn_start, **kwargs)
        finally:
            if at_turn_start:
                turn_start_done.set()

    monkeypatch.setattr(ch, "_maybe_update_session_title", _retitle)

    seen = {}
    db = ChatDatabase(":memory:")
    db.set_setting("custom_model", model)
    session = db.create_session(title="New Task", agent_type="gaia")
    sid = session["id"]

    def _fake_dispatch(sse_handler, request, history_pairs, model_id, agent, **kw):
        seen["turn_start_ran"] = turn_start_done.wait(10)
        seen["title"] = db.get_session(sid)["title"]
        seen["title_calls"] = len(_title_calls(http))
        sse_handler._emit({"type": "answer", "content": "Tea began in China."})

    monkeypatch.setattr(ch, "_dispatch_sidecar_query", _fake_dispatch)
    if slots is not None:
        http.add(responses.GET, f"{LEMONADE}/health", json=_health(slots))
    http.add(
        responses.POST,
        f"{LEMONADE}/chat/completions",
        json=_reply("History of Tea Essay"),
    )
    db.begin_turn(sid, ESSAY)
    ch._set_provisional_title(db, sid, ESSAY)
    request = ChatRequest(session_id=sid, message=ESSAY, stream=True, agent_type="gaia")

    async def _drive():
        async for _ in ch._stream_chat_impl(
            SimpleNamespace(handler=None), db, session, request
        ):
            pass
        await asyncio.gather(*list(ch._background_tasks))

    asyncio.run(_drive())
    return seen, db.get_session(sid)["title"]


@pytest.mark.parametrize("model", [CLOUD_MODEL, "amd.gpt-oss-120b"])
def test_a_cloud_chat_is_titled_while_its_first_answer_runs(
    monkeypatch, local_lemonade, sidecar_flagship, model
):
    seen, final = _relayed_turn(monkeypatch, local_lemonade, model)

    assert seen == {
        "turn_start_ran": True,
        "title": "History of Tea Essay",
        "title_calls": 1,
    }
    assert final == "History of Tea Essay"
    # Titled from the question alone: no answer existed yet.
    (body,) = _title_calls(local_lemonade)
    assert body["model"] == model
    assert "Assistant:" not in body["messages"][0]["content"]


@pytest.mark.parametrize("slots", [1, 2, 4])
def test_a_local_chat_is_titled_only_after_the_answer(
    monkeypatch, local_lemonade, sidecar_flagship, slots
):
    """A local title at turn start could take the model before the answer's
    first call, so the first token would wait for the title. A side slot
    keeps the conversation's cache, not the answer's place in line."""
    seen, final = _relayed_turn(monkeypatch, local_lemonade, LOCAL_MODEL, slots)

    assert seen == {"turn_start_ran": True, "title": PROVISIONAL, "title_calls": 0}
    assert final == "History of Tea Essay"
    (body,) = _title_calls(local_lemonade)
    assert "Assistant: Tea began in China." in body["messages"][0]["content"]


def test_a_turn_start_title_never_asks_a_local_model(local_lemonade):
    """Not even for its slots: the gate is the model's kind, not how it is loaded."""
    db, sid = _provisional_chat()

    asyncio.run(
        ch._maybe_update_session_title(
            db, sid, ESSAY, "", LOCAL_MODEL, at_turn_start=True
        )
    )

    assert len(local_lemonade.calls) == 0
    assert db.get_session(sid)["title"] == PROVISIONAL


# ── A busy one-slot model is not waited on into the next turn ────────────


def _busy_lease(monkeypatch):
    """A model lease that is never granted; returns the list of lease attempts."""
    from contextlib import contextmanager

    from gaia.daemon.broker_client import BrokerUnavailableError

    attempts = []

    @contextmanager
    def _always_busy(model, **_kw):
        attempts.append(model)
        raise BrokerUnavailableError(
            f"no model-slot lease for '{model}': not granted within its "
            "30s wait limit",
            status_code=504,
        )
        yield None  # pragma: no cover

    monkeypatch.setattr("gaia.daemon.broker_client.model_lease", _always_busy)
    monkeypatch.setattr(ch, "_TITLE_BUSY_RETRY_S", 0, raising=False)
    return attempts


def test_a_busy_one_slot_model_is_waited_for_once(local_lemonade, monkeypatch):
    """A title still waiting minutes later would land between the next turn's
    steps and evict the conversation's cache from the model's only slot."""
    attempts = _busy_lease(monkeypatch)
    local_lemonade.add(responses.GET, f"{LEMONADE}/health", json=_health(1))
    db, sid = _provisional_chat()

    asyncio.run(ch._maybe_update_session_title(db, sid, ESSAY, "Tea", LOCAL_MODEL))

    assert attempts == [LOCAL_MODEL]
    assert _title_calls(local_lemonade) == []
    assert db.get_session(sid)["title"] == PROVISIONAL
    # Not a failed try: the chat's next turn asks again.
    assert sid not in ch._AUTO_TITLE_RUNNING
    assert ch._AUTO_TITLE_FAILURES.get(sid, 0) == 0


def test_a_busy_model_whose_slots_cannot_be_read_is_waited_for_once(
    local_lemonade, monkeypatch
):
    attempts = _busy_lease(monkeypatch)
    local_lemonade.add(responses.GET, f"{LEMONADE}/health", status=500)
    db, sid = _provisional_chat()

    asyncio.run(ch._maybe_update_session_title(db, sid, ESSAY, "Tea", LOCAL_MODEL))

    assert attempts == [LOCAL_MODEL]
    assert db.get_session(sid)["title"] == PROVISIONAL


def test_a_busy_model_with_a_side_slot_is_waited_out_to_the_limit(
    local_lemonade, monkeypatch
):
    attempts = _busy_lease(monkeypatch)
    local_lemonade.add(responses.GET, f"{LEMONADE}/health", json=_health(2))
    db, sid = _provisional_chat()

    asyncio.run(ch._maybe_update_session_title(db, sid, ESSAY, "Tea", LOCAL_MODEL))

    assert attempts == [LOCAL_MODEL] * ch._TITLE_BUSY_TRIES
    assert db.get_session(sid)["title"] == PROVISIONAL


# ── An answer sent as the agent exits is never dropped ───────────────────


def test_an_answer_sent_as_the_agent_exits_is_kept(
    monkeypatch, local_lemonade, sidecar_flagship
):
    """The stream's poll of the agent's queue timed out, and in that instant
    the agent answered and exited: the stream saw it gone, dropped the
    answer, and saved the turn as empty."""
    import queue
    import threading

    from gaia.ui.sse_handler import SSEOutputHandler

    answer_sent = threading.Event()
    producer = {}

    class _AnswersDuringThePoll(queue.Queue):
        def get(self, block=True, timeout=None):
            if "thread" in producer and self.empty():
                agent = producer.pop("thread")
                answer_sent.set()
                agent.join(10)
                assert not agent.is_alive()
                raise queue.Empty
            return super().get(block, timeout)

    class _Handler(SSEOutputHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.event_queue = _AnswersDuringThePoll()

    def _fake_dispatch(sse_handler, request, history_pairs, model_id, agent, **kw):
        producer["thread"] = threading.current_thread()
        assert answer_sent.wait(10)
        sse_handler._emit({"type": "answer", "content": "Tea began in China."})

    monkeypatch.setattr("gaia.ui.sse_handler.SSEOutputHandler", _Handler)
    monkeypatch.setattr(ch, "_dispatch_sidecar_query", _fake_dispatch)
    monkeypatch.setattr(ch, "_maybe_update_session_title", AsyncMock())

    db = ChatDatabase(":memory:")
    db.set_setting("custom_model", LOCAL_MODEL)
    session = db.create_session(title="New Task", agent_type="gaia")
    db.begin_turn(session["id"], ESSAY)
    request = ChatRequest(
        session_id=session["id"], message=ESSAY, stream=True, agent_type="gaia"
    )

    async def _drive():
        return [
            e
            async for e in ch._stream_chat_impl(
                SimpleNamespace(handler=None), db, session, request
            )
        ]

    events = "".join(asyncio.run(_drive()))

    assert '"content": "Tea began in China."' in events
    messages = db.get_messages(session["id"])
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "Tea began in China."


# ── A new turn cancels the chat's pending title ──────────────────────────


def test_a_new_turn_cancels_the_chats_pending_title(
    monkeypatch, local_lemonade, sidecar_flagship
):
    """An after-turn title waiting out a busy model (up to ~6 min) could take
    the model between the next turn's steps; that turn titles the chat itself."""
    started, cancelled = [], []

    async def _waiting_title(*_args, **_kwargs):
        started.append(True)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(ch, "_title_when_model_free", _waiting_title)
    cancelled_when_agent_ran = []

    def _fake_dispatch(sse_handler, request, history_pairs, model_id, agent, **kw):
        cancelled_when_agent_ran.append(len(cancelled))
        sse_handler._emit({"type": "answer", "content": "Tea began in China."})

    monkeypatch.setattr(ch, "_dispatch_sidecar_query", _fake_dispatch)
    db = ChatDatabase(":memory:")
    db.set_setting("custom_model", LOCAL_MODEL)
    session = db.create_session(title="New Task", agent_type="gaia")
    sid = session["id"]

    async def _turn(question):
        db.begin_turn(sid, question)
        ch._set_provisional_title(db, sid, question)
        request = ChatRequest(
            session_id=sid, message=question, stream=True, agent_type="gaia"
        )
        async for _ in ch._stream_chat_impl(
            SimpleNamespace(handler=None), db, session, request
        ):
            pass

    async def _drive():
        await _turn(ESSAY)
        for _ in range(1000):
            if started:
                break
            await asyncio.sleep(0)
        assert started, "the first turn's after-turn title never started"
        db.add_message(sid, "assistant", "Tea began in China.", ends_turn=True)
        await _turn("And one about coffee?")
        # The new turn queued its own after-turn title in place of the old one.
        assert sid in ch._AUTO_TITLE_PENDING
        ch._cancel_pending_title(sid)
        await asyncio.gather(*list(ch._background_tasks), return_exceptions=True)

    asyncio.run(_drive())

    assert cancelled_when_agent_ran == [0, 1]


def test_a_turn_cancels_only_its_own_chats_title():
    async def _drive():
        never = asyncio.Event()
        mine = ch._spawn_after_turn_title(never.wait(), "chat-a")
        theirs = ch._spawn_after_turn_title(never.wait(), "chat-b")
        await asyncio.sleep(0)

        ch._cancel_pending_title("chat-a")
        await asyncio.gather(mine, return_exceptions=True)

        assert mine.cancelled() and not theirs.done()
        assert "chat-a" not in ch._AUTO_TITLE_PENDING
        ch._cancel_pending_title("chat-b")
        await asyncio.gather(theirs, return_exceptions=True)
        assert theirs.cancelled()

    asyncio.run(_drive())


def test_a_finished_title_is_forgotten_and_never_cancelled():
    async def _drive():
        async def _quick():
            return None

        done = ch._spawn_after_turn_title(_quick(), "chat-c")
        await done
        await asyncio.sleep(0)

        assert "chat-c" not in ch._AUTO_TITLE_PENDING
        ch._cancel_pending_title("chat-c")  # nothing pending: a no-op
        assert not done.cancelled()

    asyncio.run(_drive())
