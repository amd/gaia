# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Sidebar chat search: finds chats by what was said, not just their title."""

import pytest
from fastapi.testclient import TestClient

from gaia.ui.chat_search import fold, snippet
from gaia.ui.server import create_app

FIRST = "what languages does morgan speak? its in the resume file with the emoji"


@pytest.fixture
def app():
    return create_app(db_path=":memory:")


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def db(app):
    return app.state.db


def _chat(db, title, *messages):
    sid = db.create_session(title=title)["id"]
    for i, text in enumerate(messages):
        db.add_message(sid, "user" if i % 2 == 0 else "assistant", text)
    return sid


def _search(client, q, **params):
    r = client.get("/api/sessions/search", params={"q": q, **params})
    assert r.status_code == 200, r.text
    return r.json()["results"]


def test_title_only_match(client, db):
    sid = _chat(db, "Quarterly budget review")
    [hit] = _search(client, "BUDGET")
    assert hit["session_id"] == sid
    assert hit["field"] == "title"
    assert hit["snippet"] == {
        "before": "Quarterly ",
        "match": "budget",
        "after": " review",
    }


def test_word_past_the_truncated_title_matches_the_first_message(client, db):
    sid = _chat(db, FIRST[:50] + "...", FIRST, "Morgan speaks French.")
    [hit] = _search(client, "resume")
    assert hit["session_id"] == sid
    assert hit["field"] == "message"
    assert hit["snippet"]["match"] == "resume"


def test_word_only_inside_a_reply_matches(client, db):
    _chat(db, "Birds", "Name a large water bird", "The PELICAN has a throat pouch.")
    _chat(db, "Other", "nothing to see")
    [hit] = _search(client, "pelican")
    assert hit["title"] == "Birds"
    assert hit["snippet"]["before"] == "The "
    assert hit["snippet"]["match"] == "PELICAN"
    assert hit["snippet"]["after"] == " has a throat pouch."


@pytest.mark.parametrize(
    "stored,query",
    [("Send me the résumé", "resume"), ("Send me the resume", "RÉSUMÉ")],
)
def test_accents_fold_both_ways(client, db, stored, query):
    sid = _chat(db, "Job hunt", stored)
    [hit] = _search(client, query)
    assert hit["session_id"] == sid
    assert fold(hit["snippet"]["match"]) == "resume"


def test_snippet_trims_long_messages_to_the_hit(client, db):
    text = "word " * 60 + "needle in the middle " + "tail " * 60
    _chat(db, "Long", text)
    [hit] = _search(client, "needle")
    s = hit["snippet"]
    assert s["before"].startswith("…") and s["after"].endswith("…")
    assert len(s["before"]) + len(s["after"]) < 140


def test_no_match_returns_empty(client, db):
    _chat(db, "Something", "about cats")
    assert _search(client, "zebra") == []


def test_result_count_is_bounded(client, db):
    for i in range(60):
        _chat(db, f"chat {i}", "the shared word is orchid")
    assert len(_search(client, "orchid")) == 20
    assert len(_search(client, "orchid", limit=5)) == 5
    assert len(_search(client, "orchid", limit=500)) == 50


def test_more_matches_than_the_limit_are_reported(client, db):
    for i in range(3):
        _chat(db, f"chat {i}", "orchid")
    r = client.get("/api/sessions/search", params={"q": "orchid", "limit": 2})
    assert r.json()["truncated"] is True
    r = client.get("/api/sessions/search", params={"q": "orchid", "limit": 3})
    assert r.json()["truncated"] is False


def test_background_runs_do_not_push_a_real_chat_past_the_limit(client, db):
    """The sidebar hides eval and scheduled chats, so they must not fill the cap."""
    real = _chat(db, "Tea notes", "my orchid repots")
    for i in range(5):
        _chat(db, f"Eval: run {i}", "orchid scenario")
    _chat(db, "Schedule: daily digest", "orchid digest")

    hits = client.get("/api/sessions/search", params={"q": "orchid", "limit": 2}).json()
    assert [h["session_id"] for h in hits["results"]] == [real]
    assert hits["truncated"] is False

    shown = client.get(
        "/api/sessions/search",
        params={"q": "orchid", "limit": 2, "include_automated": True},
    ).json()
    assert real not in [h["session_id"] for h in shown["results"]]
    assert shown["truncated"] is True


def test_newest_chat_first(client, db):
    old = _chat(db, "Old", "orchid")
    new = _chat(db, "New", "orchid")
    db.add_message(old, "user", "bump")  # now the most recently updated
    assert [h["session_id"] for h in _search(client, "orchid")] == [old, new]


def test_private_chats_are_searchable(client, db):
    sid = db.create_session(title="Secret", private=True)["id"]
    db.add_message(sid, "user", "my locker code is in here")
    assert [h["session_id"] for h in _search(client, "locker")] == [sid]


def test_edited_answer_is_searched_by_its_new_text(client, db):
    sid = _chat(db, "Edit", "question", "first answer")
    assert _search(client, "answer")
    msg_id = db.get_messages(sid)[-1]["id"]
    db.replace_answer(sid, msg_id, "replaced reply", None, None)
    assert _search(client, "answer") == []
    assert [h["session_id"] for h in _search(client, "replaced")] == [sid]


def test_blank_query_is_rejected(client):
    assert client.get("/api/sessions/search", params={"q": ""}).status_code == 422


def test_search_path_is_not_taken_for_a_session_id(client, db):
    _chat(db, "x")
    r = client.get("/api/sessions/search", params={"q": "x"})
    assert "results" in r.json()


def test_snippet_maps_folded_positions_back_to_the_original():
    s = snippet("Straße und Ⅻ résumé", "resume")
    assert s == {"before": "Straße und Ⅻ ", "match": "résumé", "after": ""}
    assert snippet("nothing here", "zzz") is None
