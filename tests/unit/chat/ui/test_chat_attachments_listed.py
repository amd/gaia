# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A file attached in the composer is listed for its chat and can be taken off (#8812).

The composer's uploads were readable by their chat but never in its document
list, so the user couldn't see what the chat reads or remove it. Each turn now
puts the chat's attached documents in the library, attached to that chat, and
a removed one is no longer granted. ``GAIA_HOME`` and the home folder are temp
dirs; the validator is the real one.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gaia.security import PathValidator


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """A fake home, and a GAIA_HOME inside it as ``~/.gaia`` normally is."""
    from gaia.ui.utils import DOCUMENT_ROOTS_ENV

    home = (tmp_path / "home").resolve()
    home.mkdir()
    gaia_home = home / ".gaia"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("GAIA_HOME", str(gaia_home))
    monkeypatch.delenv("GAIA_ALLOWED_PATHS", raising=False)
    monkeypatch.delenv(DOCUMENT_ROOTS_ENV, raising=False)
    return home, gaia_home


@pytest.fixture
def app(homes):
    from gaia.ui.server import create_app

    with patch("gaia.ui.server._index_document", new=AsyncMock(return_value=1)):
        application = create_app(db_path=":memory:")
        yield TestClient(application), application.state.db


def _attach(client, name, body, content_type="text/markdown"):
    """Upload *name* the way the composer does; returns the message link to it."""
    resp = client.post("/api/files/upload", files={"file": (name, body, content_type)})
    assert resp.status_code == 200, resp.text
    return f"[{name}]({resp.json()['url']})"


def _send(db, session_id, text):
    """What a turn does with the user's message before the agent runs.

    Returns the documents it listed, and the uploads the chat may read.
    """
    from gaia.ui._chat_helpers import _chat_upload_paths, _list_chat_uploads
    from gaia.ui.utils import attached_upload_paths, resolve_upload_links

    message = resolve_upload_links(text)
    db.add_message(session_id, "user", message)
    attached = attached_upload_paths(message)
    _ids, listed = _list_chat_uploads(
        db, session_id, db.get_session(session_id)["document_ids"], attached
    )
    return listed, _chat_upload_paths(db, session_id, attached)


def _validator(db, session_id, uploads):
    """The chat's validator, scoped the way a turn scopes it."""
    from gaia.ui._chat_helpers import (
        _compute_allowed_paths,
        _resolve_rag_paths,
        _with_attachments,
    )

    rag, library = _resolve_rag_paths(
        db, db.get_session(session_id).get("document_ids", [])
    )
    return PathValidator(
        _with_attachments(_compute_allowed_paths(rag + library), uploads)
    )


def _chat_upload_paths_for(db, session_id):
    from gaia.ui._chat_helpers import _chat_upload_paths

    return _chat_upload_paths(db, session_id, [])


def _session_docs(client, session_id):
    resp = client.get(f"/api/sessions/{session_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()["document_ids"]


def test_an_attachment_is_listed_for_its_own_chat_only(app):
    from gaia.ui import session_environment

    client, db = app
    chat_a = db.create_session(title="A")
    chat_b = db.create_session(title="B")
    listed, readable = _send(
        db, chat_a["id"], "What is this?\n\n" + _attach(client, "brief.md", b"ALPHA-7")
    )

    assert [d["filename"] for d in listed] == ["brief.md"]
    library = client.get("/api/documents").json()["documents"]
    assert [d["id"] for d in library] == [listed[0]["id"]]
    assert _session_docs(client, chat_a["id"]) == [listed[0]["id"]]
    assert _session_docs(client, chat_b["id"]) == []

    shown_a = session_environment.describe(db, db.get_session(chat_a["id"]))
    documents_a = [e for e in shown_a["folders"] if e["source"] == "document"]
    assert [e["name"] for e in documents_a] == ["brief.md"]
    shown_b = session_environment.describe(db, db.get_session(chat_b["id"]))
    assert not [e for e in shown_b["folders"] if e["source"] == "document"]

    upload = readable[0]
    assert _validator(db, chat_a["id"], readable).validate_read(upload)[0]
    others = _chat_upload_paths_for(db, chat_b["id"])
    assert not _validator(db, chat_b["id"], others).validate_read(upload)[0]


def test_removing_an_attachment_takes_it_off_the_chat(app):
    client, db = app
    chat = db.create_session(title="A")
    listed, readable = _send(
        db, chat["id"], "Read it\n\n" + _attach(client, "brief.md", b"ALPHA-7")
    )
    upload = readable[0]

    resp = client.delete(f"/api/sessions/{chat['id']}/documents/{listed[0]['id']}")
    assert resp.status_code == 200, resp.text

    # The next turn leaves it off, and the chat can no longer read the upload.
    relisted, readable = _send(db, chat["id"], "Read it again")
    assert relisted == []
    assert _session_docs(client, chat["id"]) == []
    assert upload not in readable
    validator = _validator(db, chat["id"], readable)
    assert not validator.validate_read(upload, prompt_user=False)[0]
    assert "attach it to this chat" in validator.access_denied_message(upload)


def test_attaching_it_again_from_the_library_lets_the_chat_read_it(app):
    client, db = app
    chat = db.create_session(title="A")
    listed, _ = _send(
        db, chat["id"], "Read it\n\n" + _attach(client, "brief.md", b"ALPHA-7")
    )
    doc_id = listed[0]["id"]
    client.delete(f"/api/sessions/{chat['id']}/documents/{doc_id}")

    resp = client.post(
        f"/api/sessions/{chat['id']}/documents", json={"document_id": doc_id}
    )
    assert resp.status_code == 200, resp.text

    _, readable = _send(db, chat["id"], "Read it now")
    assert len(readable) == 1
    assert _validator(db, chat["id"], readable).validate_read(readable[0])[0]


def test_an_attachment_already_in_the_library_uses_that_document(app):
    """Removing the matching library document takes the upload away too."""
    client, db = app
    resp = client.post(
        "/api/documents/upload",
        files={"file": ("memo.md", b"same words", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    existing = resp.json()
    chat = db.create_session(title="A")

    listed, readable = _send(
        db,
        chat["id"],
        "Read it\n\n" + _attach(client, "copy of memo.md", b"same words"),
    )
    assert [d["id"] for d in listed] == [existing["id"]]
    assert len(client.get("/api/documents").json()["documents"]) == 1

    client.delete(f"/api/sessions/{chat['id']}/documents/{existing['id']}")
    _, after = _send(db, chat["id"], "Again")
    assert readable[0] not in after


def test_deleting_the_library_document_keeps_the_upload_closed(app):
    client, db = app
    chat = db.create_session(title="A")
    listed, readable = _send(
        db, chat["id"], "Read it\n\n" + _attach(client, "brief.md", b"ALPHA-7")
    )

    assert client.delete(f"/api/documents/{listed[0]['id']}").status_code == 200

    relisted, after = _send(db, chat["id"], "Again")
    assert relisted == []
    assert readable[0] not in after


def test_images_are_read_but_not_listed(app):
    client, db = app
    chat = db.create_session(title="A")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    link = "!" + _attach(client, "shot.png", png, content_type="image/png")

    listed, readable = _send(db, chat["id"], "What is this?\n\n" + link)

    assert listed == []
    assert client.get("/api/documents").json()["documents"] == []
    assert len(readable) == 1
    assert _validator(db, chat["id"], readable).validate_read(readable[0])[0]


def test_an_attachment_is_listed_once(app):
    client, db = app
    chat = db.create_session(title="A")
    first, _ = _send(
        db, chat["id"], "Read it\n\n" + _attach(client, "brief.md", b"ALPHA-7")
    )
    again, readable = _send(db, chat["id"], "And now?")

    assert len(first) == 1
    assert again == []
    assert _session_docs(client, chat["id"]) == [first[0]["id"]]
    assert len(readable) == 1


def test_the_turn_tells_the_app_about_the_listed_attachment(app):
    """``done`` carries the row, so the panel and library show it at once."""
    from gaia.ui._chat_helpers import _add_indexed_documents_to_library
    from gaia.ui.turn_summary import TurnLedger

    client, db = app
    chat = db.create_session(title="A")
    listed, _ = _send(
        db, chat["id"], "Read it\n\n" + _attach(client, "brief.md", b"ALPHA-7")
    )
    handler = MagicMock()
    handler.turn_ledger = TurnLedger()

    documents, notices = asyncio.run(
        _add_indexed_documents_to_library(db, chat["id"], handler, listed)
    )

    assert notices == []
    assert [(d["id"], d["filename"]) for d in documents] == [
        (listed[0]["id"], "brief.md")
    ]


def test_attaching_a_removed_file_again_reopens_it(app):
    client, db = app
    chat = db.create_session(title="A")
    listed, readable = _send(
        db, chat["id"], "Read it\n\n" + _attach(client, "brief.md", b"ALPHA-7")
    )
    upload = readable[0]
    resp = client.delete(f"/api/sessions/{chat['id']}/documents/{listed[0]['id']}")
    assert resp.status_code == 200, resp.text

    relisted, readable = _send(
        db, chat["id"], "Here it is again\n\n" + _attach(client, "brief.md", b"ALPHA-7")
    )
    assert upload in readable
    assert [d["filename"] for d in relisted] == ["brief.md"]
    assert _session_docs(client, chat["id"]) == [listed[0]["id"]]


def test_a_removed_file_stays_off_when_another_chat_attaches_it(app):
    client, db = app
    chat_a = db.create_session(title="A")
    chat_b = db.create_session(title="B")
    listed, readable = _send(
        db, chat_a["id"], "Read it\n\n" + _attach(client, "brief.md", b"ALPHA-7")
    )
    upload = readable[0]
    client.delete(f"/api/sessions/{chat_a['id']}/documents/{listed[0]['id']}")

    _send(db, chat_b["id"], "Mine now\n\n" + _attach(client, "brief.md", b"ALPHA-7"))
    relisted, readable = _send(db, chat_a["id"], "Read it again")

    assert relisted == []
    assert upload not in readable
    assert _session_docs(client, chat_a["id"]) == []
