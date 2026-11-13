# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A chat's title never spells out a fact the user marked sensitive.

Round 5 of the v0.26.0-rc18 test: "Remember privately: I take 50mg of
sertraline every night. Mark it sensitive." named its chat "Remember
sertraline 50mg nightly", in the sidebar on every screen, and the title stayed
after the memory was forgotten. A title the user typed is never touched.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import gaia.ui._chat_helpers as ch
import gaia.ui.routers.memory as memory_router_mod
from gaia.agents.base.memory_store import MemoryStore
from gaia.ui.database import HIDDEN_TITLE, ChatDatabase

SAID = "Remember privately: I take 50mg of sertraline every night. Mark it sensitive."
FACT = "Takes 50mg of sertraline every night"
NAMED = "Remember sertraline 50mg nightly"
CLOUD_MODEL = "fireworks.deepseek-v4p1-flash"
LEMONADE = "http://127.0.0.1:18767/api/v1"
LONG_AGO = "2020-01-01T00:00:00+00:00"


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    memory_router_mod._store = db
    yield db
    memory_router_mod._store = None
    db.close()


@pytest.fixture(autouse=True)
def _fresh_title_state():
    for state in (
        ch._AUTO_TITLE_SETTLED,
        ch._AUTO_TITLE_FAILURES,
        ch._AUTO_TITLE_ATTEMPTS,
    ):
        state.clear()


def _chat(db: ChatDatabase, title: str = "New Task") -> str:
    """A chat GAIA named *title*; a non-placeholder title at creation is typed."""
    sid = db.create_session(title="New Task")["id"]
    if title != "New Task":
        assert db.replace_auto_title(sid, "New Task", title)
    return sid


def _retitle(db, sid, reply, message=SAID, at_turn_start=False):
    gen = AsyncMock(return_value=reply)
    with (
        patch.object(ch, "_generate_session_title", gen),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
            return_value=LEMONADE,
        ),
    ):
        asyncio.run(
            ch._maybe_update_session_title(
                db, sid, message, "", CLOUD_MODEL, at_turn_start=at_turn_start
            )
        )
    return gen


# -- What names a private fact ----------------------------------------------


def test_a_title_names_a_sensitive_fact_by_its_distinctive_words(store):
    store.store(category="fact", content=FACT, sensitive=True)
    store.store(category="fact", content="My bonus is 9,000 euros", sensitive=True)

    assert store.names_private_fact(NAMED)
    assert store.names_private_fact("Sertraline side effects")
    assert store.names_private_fact("A 9,000 question")
    assert not store.names_private_fact("Weekend plans")
    assert not store.names_private_fact("Back pain every night")


def test_a_forgotten_fact_names_only_chats_from_before_it_was_forgotten(store):
    kid = store.store(category="fact", content=FACT, sensitive=True)
    store.forget_with_copies(kid)

    assert store.names_private_fact(NAMED, since=LONG_AGO)
    assert not store.names_private_fact(NAMED, since="2999-01-01T00:00:00+00:00")
    # A plain row the user forgot is not private.
    plain = store.store(category="fact", content="I live in Calgary")
    store.forget_with_copies(plain)
    assert not store.names_private_fact("Moving to Calgary", since=LONG_AGO)


# -- The turn that saved it ------------------------------------------------


def test_the_question_that_marks_a_fact_private_does_not_name_the_chat(store):
    db = ChatDatabase(":memory:")
    sid = _chat(db)
    db.begin_turn(sid, SAID)

    ch._set_provisional_title(db, sid, SAID)
    turn_start = _retitle(db, sid, NAMED, at_turn_start=True)

    turn_start.assert_not_awaited()
    assert db.get_session(sid)["title"] == "New Task"


def test_a_written_title_naming_the_fact_is_not_used(store):
    db = ChatDatabase(":memory:")
    sid = _chat(db)
    db.begin_turn(sid, SAID)
    store.store(category="fact", content=FACT, sensitive=True)

    _retitle(db, sid, NAMED)
    assert db.get_session(sid)["title"] == "New Task"

    _retitle(db, sid, "Medication reminder")
    assert db.get_session(sid)["title"] == "Medication reminder"


def test_a_question_title_shown_during_the_turn_goes_once_the_fact_is_saved(store):
    """ "Remember privately" alone is not a sensitivity note, so the question
    names the chat until the turn has saved the fact."""
    question = "Remember privately: I take 50mg of sertraline every night"
    db = ChatDatabase(":memory:")
    sid = _chat(db)
    db.begin_turn(sid, question)
    ch._set_provisional_title(db, sid, question)
    assert db.get_session(sid)["title"] == question

    store.store(category="fact", content=FACT, sensitive=True)
    _retitle(db, sid, None, message=question)

    assert db.get_session(sid)["title"] == HIDDEN_TITLE


# -- Forgetting it ----------------------------------------------------------


@pytest.fixture
def client(store):
    from gaia.ui.dependencies import get_db

    app = FastAPI()
    app.include_router(memory_router_mod.router)
    db = ChatDatabase(":memory:")
    app.dependency_overrides[get_db] = lambda: db
    provider = MagicMock()
    provider.embed.return_value = [[0.5] * 768]
    with patch("gaia.llm.providers.lemonade.LemonadeProvider", return_value=provider):
        with TestClient(app, headers={"X-Gaia-UI": "1"}) as c:
            c.db = db
            yield c
    db.close()


def test_forgetting_on_the_memory_page_renames_the_chats_it_named(store, client):
    db = client.db
    named = _chat(db, NAMED)
    typed = db.create_session(title="Sertraline notes")["id"]
    other = _chat(db, "Weekend plans")
    before = db.get_session(named)["updated_at"]
    kid = store.store(category="fact", content=FACT, sensitive=True)

    assert client.delete(f"/api/memory/knowledge/{kid}").status_code == 200

    assert db.get_session(named)["title"] == HIDDEN_TITLE
    assert db.get_session(named)["updated_at"] == before
    assert db.get_session(typed)["title"] == "Sertraline notes"
    assert db.get_session(other)["title"] == "Weekend plans"


def test_forgetting_a_plain_fact_renames_nothing(store, client):
    db = client.db
    sid = _chat(db, "Moving to Calgary")
    kid = store.store(category="fact", content="I live in Calgary")

    client.delete(f"/api/memory/knowledge/{kid}")

    assert db.get_session(sid)["title"] == "Moving to Calgary"


def test_forgetting_in_a_chat_renames_the_other_chats_at_the_next_turn(store):
    db = ChatDatabase(":memory:")
    named = _chat(db, NAMED)
    kid = store.store(category="fact", content=FACT, sensitive=True)
    store.forget_with_copies(kid)
    sid = _chat(db)
    db.begin_turn(sid, "Plan my weekend")

    _retitle(db, sid, "Weekend plans", message="Plan my weekend")

    assert db.get_session(named)["title"] == HIDDEN_TITLE
    assert db.get_session(sid)["title"] == "Weekend plans"
