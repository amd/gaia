# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Each chat reads only the documents attached to it (#8379).

Every document dragged into any chat used to be copied into GAIA's documents
folder, which every chat is granted, so one chat could list and read another's
copies, and a copy taken off a chat stayed readable through that folder. Copies
now live in GAIA's chat data, where only an exact grant opens anything. These
use the real validator and, where it matters, the real index; ``GAIA_HOME`` and
the home folder are temp dirs.
"""

import os
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.rag.sdk import RAGSDK, RAGConfig
from gaia.security import PathValidator


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


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
def copies(homes):
    """Two chats' document copies, in GAIA's chat data."""
    from gaia.ui.utils import document_copies_dir

    folder = document_copies_dir()
    folder.mkdir(parents=True)
    mine = folder / "mine.txt"
    theirs = folder / "theirs.txt"
    mine.write_text("My code is MINE-1.", encoding="utf-8")
    theirs.write_text("Their code is THEIRS-2.", encoding="utf-8")
    return folder.resolve(), str(mine.resolve()), str(theirs.resolve())


def _chat_validator(*attached):
    """A chat's validator, scoped the way the Agent UI scopes it."""
    from gaia.ui._chat_helpers import _compute_allowed_paths

    validator = PathValidator(_compute_allowed_paths(list(attached)))
    asked = []

    def approve(path):
        asked.append(path)
        return True

    # A user who would say yes to anything, as full access answers.
    validator.set_access_prompt(approve)
    return validator, asked


# ── The validator ────────────────────────────────────────────────────────


def test_a_chat_reads_its_own_copy_and_not_another_chats(copies):
    _, mine, theirs = copies
    validator, asked = _chat_validator(mine)

    assert validator.validate_read(mine)[0]
    allowed, reason = validator.validate_read(theirs)
    assert not allowed
    assert "not attached to this chat" in reason
    assert "attach it to this chat" in reason
    assert not validator.is_path_allowed(theirs)
    assert asked == [], "another chat's copy must never be offered for approval"


def test_the_scope_never_grants_the_copies_folder(copies):
    folder, mine, _ = copies
    validator, _ = _chat_validator(mine)

    granted = {Path(p) for p in validator.allowed_paths}
    assert folder not in granted
    assert not any(folder in p.parents for p in granted if p != Path(mine))
    assert not validator.is_path_allowed(str(folder), prompt_user=False)
    assert not validator.validate_read(str(folder), prompt_user=False)[0]


def test_a_chat_with_no_documents_reaches_no_copy(copies):
    _, mine, theirs = copies
    validator, asked = _chat_validator()

    for path in (mine, theirs):
        assert not validator.validate_read(path)[0]
    assert asked == []


@pytest.mark.parametrize(
    "grant",
    ["gaia_home", "chat_data", "copies_folder", "home"],
)
def test_no_wider_grant_opens_another_chats_copy(copies, homes, grant):
    """A folder that holds the copies is not a grant of them."""
    home, gaia_home = homes
    folder, mine, theirs = copies
    wide = {
        "gaia_home": gaia_home,
        "chat_data": gaia_home / "chat",
        "copies_folder": folder,
        "home": home,
    }[grant]
    validator = PathValidator([str(wide), mine])

    assert validator.is_path_allowed(mine, prompt_user=False)
    assert not validator.is_path_allowed(theirs, prompt_user=False)
    assert not validator.validate_read(theirs, prompt_user=False)[0]


def test_the_chat_database_is_never_readable(homes):
    """Every chat's messages live there; no grant of a folder opens it."""
    _, gaia_home = homes
    database = gaia_home / "chat" / "gaia_chat.db"
    database.parent.mkdir(parents=True)
    sqlite3.connect(database).close()
    validator = PathValidator([str(gaia_home / "chat")])
    validator.set_access_prompt(lambda _p: True)

    assert not validator.validate_read(str(database))[0]
    assert not validator.is_path_allowed(str(database))


def test_another_chats_message_attachment_stays_closed(homes):
    """Files attached to messages sit beside the copies and get the same rule."""
    from gaia.ui._chat_helpers import _compute_allowed_paths, _with_attachments
    from gaia.ui.utils import uploads_dir

    folder = uploads_dir()
    folder.mkdir(parents=True)
    mine = folder / "notes.txt"
    theirs = folder / "salary.txt"
    mine.write_text("mine", encoding="utf-8")
    theirs.write_text("theirs", encoding="utf-8")
    validator = PathValidator(
        _with_attachments(_compute_allowed_paths([]), [str(mine.resolve())])
    )
    validator.set_access_prompt(lambda _p: True)

    assert validator.validate_read(str(mine))[0]
    assert not validator.validate_read(str(theirs))[0]
    assert not validator.validate_read(str(folder))[0]


@pytest.mark.parametrize(
    "spelling",
    ["dotdot", "trailing_sep", "case", "relative"],
)
def test_path_tricks_do_not_reach_another_chats_copy(
    copies, homes, monkeypatch, spelling
):
    folder, mine, theirs = copies
    _, gaia_home = homes
    if spelling == "case" and os.path.normcase("A") != "a":
        pytest.skip("Case-sensitive filesystem: another case is another file")
    path = {
        "dotdot": str(
            folder
            / ".."
            / ".."
            / "documents"
            / ".."
            / "chat"
            / "documents"
            / "theirs.txt"
        ),
        "trailing_sep": theirs + os.sep,
        "case": theirs.upper(),
        "relative": os.path.join("..", "documents", "theirs.txt"),
    }[spelling]
    if spelling == "relative":
        (folder.parent / "uploads").mkdir(exist_ok=True)
        monkeypatch.chdir(folder.parent / "uploads")
    validator = PathValidator([str(gaia_home), mine])
    validator.set_access_prompt(lambda _p: True)

    assert not validator.validate_read(path)[0]
    assert not validator.is_path_allowed(path)


def test_a_grant_of_one_copy_does_not_open_a_name_that_extends_it(copies):
    folder, mine, _ = copies
    twin = folder / "mine.txt.bak"
    twin.write_text("backup", encoding="utf-8")
    validator = PathValidator([mine])

    assert validator.is_path_allowed(mine, prompt_user=False)
    assert not validator.is_path_allowed(str(twin), prompt_user=False)


def test_a_link_into_the_copies_is_followed_and_refused(copies, homes):
    """A link from a granted folder into the copies does not launder them."""
    folder, mine, theirs = copies
    _, gaia_home = homes
    shared = gaia_home / "documents"
    shared.mkdir(parents=True)
    link = shared / "looks-harmless.txt"
    try:
        link.symlink_to(theirs)
    except (OSError, NotImplementedError):
        pytest.skip("This OS account cannot create symlinks")
    validator = PathValidator([str(shared), mine])

    assert not validator.is_path_allowed(str(link), prompt_user=False)
    assert not validator.validate_read(str(link), prompt_user=False)[0]


def test_a_read_only_grant_does_not_open_the_copies(copies, homes):
    _, gaia_home = homes
    _, mine, theirs = copies
    validator = PathValidator([mine])
    validator.read_only_paths.add(Path(os.path.realpath(gaia_home)))

    assert not validator.validate_read(theirs, prompt_user=False)[0]


def test_taken_off_then_attached_again(copies):
    """Removing the grant refuses the copy with the right advice; re-adding restores it."""
    _, mine, _ = copies
    validator, asked = _chat_validator(mine)

    validator.remove_allowed_path(mine)
    allowed, reason = validator.validate_read(mine)
    assert not allowed
    assert "attach it to this chat" in reason
    assert "Environment" not in reason
    assert asked == []

    validator.add_allowed_path(mine)
    assert validator.validate_read(mine)[0]


# ── The refusal message for a withheld document ──────────────────────────


def test_a_withheld_document_says_to_attach_it_again(tmp_path):
    doc = tmp_path / "report.pdf"
    doc.write_bytes(b"%PDF")
    validator = PathValidator([str(tmp_path)])
    validator.withhold_path(str(doc))

    allowed, reason = validator.validate_read(str(doc), prompt_user=False)

    assert not allowed
    assert "took 'report.pdf' off this chat" in reason
    assert "attach it to this chat again" in reason
    assert "Environment" not in reason
    assert "folders" not in reason
    assert "taken off this chat" in validator.grant_refusal_reason(str(doc))


def test_a_document_already_deleted_still_says_to_attach_it_again(tmp_path):
    """A library document deleted before it is taken off keeps the right advice."""
    gone = tmp_path / "deleted.pdf"
    validator = PathValidator([str(tmp_path)])
    validator.withhold_path(str(gone), document=True)

    reason = validator.validate_read(str(gone), prompt_user=False)[1]

    assert "attach it to this chat again" in reason
    assert "Environment" not in reason
    assert validator.is_withheld_document(str(gone))


def test_a_withheld_document_keeps_its_advice_on_a_rebuilt_validator(tmp_path):
    """What a rebuild copies over: the path and whether it was a document."""
    doc = tmp_path / "notes.txt"
    doc.write_text("x", encoding="utf-8")
    old = PathValidator([str(tmp_path)])
    old.withhold_path(str(doc))
    doc.unlink()

    new = PathValidator([str(tmp_path)])
    for path in old.withheld_paths:
        new.withhold_path(str(path), document=old.is_withheld_document(path))

    reason = new.validate_read(str(doc), prompt_user=False)[1]
    assert "attach it to this chat again" in reason


def test_a_withheld_folder_is_not_a_document_even_when_told_nothing(tmp_path):
    folder = tmp_path / "v1.2"
    folder.mkdir()
    validator = PathValidator([str(tmp_path)])
    validator.withhold_path(str(folder))

    assert not validator.is_withheld_document(str(folder))
    reason = validator.validate_read(str(folder / "a.txt"), prompt_user=False)[1]
    assert "under Environment in the session panel" in reason


def test_a_withheld_folder_still_points_at_environment(tmp_path):
    folder = tmp_path / "private"
    folder.mkdir()
    validator = PathValidator([str(tmp_path)])
    validator.withhold_path(str(folder))

    reason = validator.validate_read(str(folder / "a.txt"), prompt_user=False)[1]

    assert "under Environment in the session panel" in reason


# ── The index ────────────────────────────────────────────────────────────


def _index(tmp_path, validator):
    """A real index with fake vectors, checking paths with *validator*."""
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(
            RAGConfig(
                cache_dir=str(tmp_path / "rag-cache"),
                allowed_paths=[str(p) for p in validator.allowed_paths],
                use_llm_chunking=False,
            ),
            path_validator=validator,
        )
    sdk._load_embedder = lambda: None
    sdk._encode_texts = lambda texts, **_: np.ones((len(texts), 4), dtype="float32")
    sdk._encode_query = lambda _q: np.ones((1, 4), dtype="float32")
    sdk._get_hmac_key = lambda: b"test-cache-key" * 3
    return sdk


class _Handler:
    def _emit(self, _event):
        pass

    def print_tool_usage(self, _name):
        pass

    def pretty_print_json(self, *_args, **_kwargs):
        pass


class _Agent(RAGToolsMixin):
    def __init__(self, rag, validator):
        self.rag = rag
        self.path_validator = validator
        self.indexed_files = set()
        self.attached_documents = []
        self.current_session = None
        self.max_chunks = 5
        self._cancel_event = None
        self.rebuild_system_prompt = MagicMock()

    def _generate_search_keys(self, query):
        return [query]


def _query(path, query):
    return _TOOL_REGISTRY["query_specific_file"]["function"](
        file_path=path, query=query
    )


def test_the_index_will_not_read_another_chats_copy(tmp_path, copies):
    """Asking for another chat's copy by its path indexes nothing from it."""
    _, mine, theirs = copies
    validator, asked = _chat_validator(mine)
    agent = _Agent(_index(tmp_path, validator), validator)
    agent.register_rag_tools()

    result = _query(theirs, "their code")

    assert result["status"] == "error", result
    assert "THEIRS-2" not in str(result)
    assert str(Path(theirs).absolute()) not in agent.rag.indexed_files
    assert "MINE-1" in str(_query(mine, "my code"))
    assert asked == []


def test_a_shared_index_cache_does_not_hand_over_another_chats_copy(tmp_path, copies):
    """Chat B indexed its copy; chat A on the same cache still can't load it."""
    _, mine, theirs = copies
    other, _ = _chat_validator(theirs)
    _Agent(_index(tmp_path, other), other).rag.index_document(theirs)

    validator, _ = _chat_validator(mine)
    agent = _Agent(_index(tmp_path, validator), validator)
    agent.register_rag_tools()

    result = _query(theirs, "their code")
    assert result["status"] == "error", result
    assert "THEIRS-2" not in str(result)


def test_a_relayed_chat_drops_a_detached_copy_for_good(tmp_path, copies):
    """The sidecar path: taken off, the copy is refused; attached again, it reads."""
    server = pytest.importorskip("gaiapy.server")
    _, mine, theirs = copies
    validator, asked = _chat_validator(mine, theirs)
    agent = _Agent(_index(tmp_path, validator), validator)
    agent.register_rag_tools()
    server._index_attached_documents(agent, [mine, theirs], _Handler())

    server._index_attached_documents(agent, [mine], _Handler())
    result = _query(theirs, "their code")
    assert result["status"] == "error", result
    assert "THEIRS-2" not in str(result)
    reason = validator.validate_read(theirs)[1]
    assert "attach it to this chat" in reason
    assert "Environment" not in reason
    assert asked == []

    server._index_attached_documents(agent, [mine, theirs], _Handler())
    assert "THEIRS-2" in str(_query(theirs, "their code"))


# ── The Agent UI: uploads, sessions, the move out of the shared folder ──


@pytest.fixture
def app(homes):
    from gaia.ui.server import create_app

    with patch("gaia.ui.server._index_document", new=AsyncMock(return_value=1)):
        application = create_app(db_path=":memory:")
        yield TestClient(application), application.state.db


def _upload(client, name, body):
    resp = client.post(
        "/api/documents/upload", files={"file": (name, body, "text/plain")}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_two_chats_uploads_stay_with_their_own_chat(app, homes):
    from gaia.ui import session_environment

    client, db = app
    _, gaia_home = homes
    doc_a = _upload(client, "a.txt", b"chat A secret ALPHA")
    doc_b = _upload(client, "b.txt", b"chat B secret BRAVO")
    assert Path(doc_a["filepath"]).parent == (gaia_home / "chat" / "documents")
    session_a = db.create_session(title="A")
    session_b = db.create_session(title="B")
    for session, doc in ((session_a, doc_a), (session_b, doc_b)):
        resp = client.post(
            f"/api/sessions/{session['id']}/documents",
            json={"document_id": doc["id"]},
        )
        assert resp.status_code == 200, resp.text

    for own, other, session in (
        (doc_a, doc_b, session_a),
        (doc_b, doc_a, session_b),
    ):
        scope = session_environment._computed_scope(db, db.get_session(session["id"]))
        validator = PathValidator(scope)
        validator.set_access_prompt(lambda _p: True)
        assert validator.validate_read(own["filepath"])[0]
        assert not validator.validate_read(other["filepath"])[0]

    # Detached, the copy leaves the chat's scope.
    client.delete(f"/api/sessions/{session_a['id']}/documents/{doc_a['id']}")
    scope = session_environment._computed_scope(db, db.get_session(session_a["id"]))
    assert not PathValidator(scope).validate_read(doc_a["filepath"])[0]


def test_deleting_an_uploaded_document_removes_its_copy(app):
    client, _ = app
    doc = _upload(client, "gone.txt", b"to be deleted")
    stored = Path(doc["filepath"])
    assert stored.is_file()

    assert client.delete(f"/api/documents/{doc['id']}").status_code == 200
    assert not stored.exists()


def _library_db(tmp_path):
    from gaia.ui.database import ChatDatabase

    return ChatDatabase(str(tmp_path / "library.db"))


def _add(db, path, content):
    path.write_text(content, encoding="utf-8")
    return db.add_document(
        filename=path.name,
        filepath=str(path),
        file_hash=f"hash-{path.name}",
        file_size=len(content),
    )


def test_old_copies_move_out_of_the_shared_folder(tmp_path, homes):
    from gaia.ui.document_copies import move_library_files_out_of_shared_folder
    from gaia.ui.utils import document_copies_dir, managed_documents_dir

    db = _library_db(tmp_path)
    shared = managed_documents_dir()
    (shared / "sub").mkdir(parents=True)
    old = _add(db, shared / "old.txt", "OLD-COPY")
    nested = _add(db, shared / "sub" / "kept.txt", "NESTED")
    workspace = shared / "agent-notes.md"
    workspace.write_text("not in the library", encoding="utf-8")
    elsewhere = tmp_path / "user.txt"
    user = _add(db, elsewhere, "USER")

    assert move_library_files_out_of_shared_folder(db) == []

    moved = Path(db.get_document(old["id"])["filepath"])
    assert moved.parent == document_copies_dir()
    assert moved.read_text(encoding="utf-8") == "OLD-COPY"
    assert not (shared / "old.txt").exists()
    assert db.get_document(nested["id"])["filepath"] == nested["filepath"]
    assert db.get_document(user["id"])["filepath"] == str(elsewhere)
    assert workspace.is_file()
    # Every chat's scope still holds the shared folder; the copy is out of it.
    validator, _ = _chat_validator()
    assert not validator.validate_read(str(moved))[0]


def test_the_move_runs_once(tmp_path, homes):
    from gaia.ui.document_copies import move_library_files_out_of_shared_folder
    from gaia.ui.utils import managed_documents_dir

    db = _library_db(tmp_path)
    shared = managed_documents_dir()
    shared.mkdir(parents=True)
    move_library_files_out_of_shared_folder(db)
    later = _add(db, shared / "saved-later.md", "the agent saved this here")

    assert move_library_files_out_of_shared_folder(db) == []
    assert db.get_document(later["id"])["filepath"] == later["filepath"]


def test_a_name_taken_in_the_copies_folder_keeps_both(tmp_path, homes):
    from gaia.ui.document_copies import move_library_files_out_of_shared_folder
    from gaia.ui.utils import document_copies_dir, managed_documents_dir

    db = _library_db(tmp_path)
    shared = managed_documents_dir()
    shared.mkdir(parents=True)
    document_copies_dir().mkdir(parents=True)
    (document_copies_dir() / "same.txt").write_text("already here", encoding="utf-8")
    doc = _add(db, shared / "same.txt", "moving in")

    assert move_library_files_out_of_shared_folder(db) == []

    moved = Path(db.get_document(doc["id"])["filepath"])
    assert moved.name != "same.txt"
    assert moved.read_text(encoding="utf-8") == "moving in"
    assert (document_copies_dir() / "same.txt").read_text(
        encoding="utf-8"
    ) == "already here"


def test_a_file_that_cannot_move_is_reported_and_retried(tmp_path, homes, caplog):
    from gaia.ui import document_copies
    from gaia.ui.utils import document_copies_dir, managed_documents_dir

    db = _library_db(tmp_path)
    shared = managed_documents_dir()
    shared.mkdir(parents=True)
    doc = _add(db, shared / "locked.txt", "LOCKED")

    with patch.object(
        document_copies.os, "replace", side_effect=PermissionError(13, "in use")
    ):
        failures = document_copies.move_library_files_out_of_shared_folder(db)

    assert len(failures) == 1 and "locked.txt" in failures[0]
    assert "every chat can read them" in caplog.text
    assert db.get_document(doc["id"])["filepath"] == str(shared / "locked.txt")
    assert db.get_setting(document_copies.MOVED_SETTING) is None
    assert list(document_copies_dir().iterdir()) == [], "no placeholder left behind"

    assert document_copies.move_library_files_out_of_shared_folder(db) == []
    assert Path(db.get_document(doc["id"])["filepath"]).parent == document_copies_dir()


def test_a_library_update_failure_puts_the_file_back(tmp_path, homes):
    from gaia.ui import document_copies
    from gaia.ui.utils import managed_documents_dir

    db = _library_db(tmp_path)
    shared = managed_documents_dir()
    shared.mkdir(parents=True)
    doc = _add(db, shared / "keep.txt", "KEEP")

    with patch.object(
        db, "update_document_path", side_effect=sqlite3.OperationalError("locked")
    ):
        failures = document_copies.move_library_files_out_of_shared_folder(db)

    assert failures
    assert (shared / "keep.txt").read_text(encoding="utf-8") == "KEEP"
    assert db.get_document(doc["id"])["filepath"] == str(shared / "keep.txt")


def test_a_file_that_cannot_be_put_back_is_reported_not_a_crash(tmp_path, homes):
    """Startup survives a failed rollback and says where the file ended up."""
    from gaia.ui import document_copies
    from gaia.ui.utils import managed_documents_dir

    db = _library_db(tmp_path)
    shared = managed_documents_dir()
    shared.mkdir(parents=True)
    _add(db, shared / "stuck.txt", "STUCK")
    real_replace = os.replace
    calls = []

    def replace_once(src, dst):
        calls.append((src, dst))
        if len(calls) > 1:
            raise PermissionError("in use")
        return real_replace(src, dst)

    with (
        patch.object(
            db, "update_document_path", side_effect=sqlite3.OperationalError("locked")
        ),
        patch.object(document_copies.os, "replace", side_effect=replace_once),
    ):
        failures = document_copies.move_library_files_out_of_shared_folder(db)

    assert len(failures) == 1
    stranded = calls[0][1]
    assert Path(stranded).read_text(encoding="utf-8") == "STUCK"
    assert str(stranded) in failures[0]
    assert "by hand" in failures[0]
    assert db.get_setting(document_copies.MOVED_SETTING) != "1"


def test_a_link_in_the_shared_folder_is_not_moved(tmp_path, homes):
    from gaia.ui.document_copies import move_library_files_out_of_shared_folder
    from gaia.ui.utils import managed_documents_dir

    db = _library_db(tmp_path)
    shared = managed_documents_dir()
    shared.mkdir(parents=True)
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    link = shared / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("This OS account cannot create symlinks")
    doc = db.add_document(
        filename="link.txt", filepath=str(link), file_hash="hash-link", file_size=7
    )

    assert move_library_files_out_of_shared_folder(db) == []
    assert db.get_document(doc["id"])["filepath"] == str(link)
    assert link.is_symlink()


# ── Review follow-ups: writes, earlier uploads, GAIA_CONFIG_DIR, shared rows ──


def test_an_attached_copy_can_be_read_but_not_changed(copies):
    """Chats share one copy per content; editing it would edit theirs."""
    _, mine, _ = copies
    validator, asked = _chat_validator(mine)

    assert validator.validate_read(mine)[0]
    blocked, reason = validator.is_write_blocked(mine)
    assert blocked
    assert "new file" in reason
    assert validator.grant_refusal_reason(mine)
    assert asked == []


def test_a_new_file_cannot_be_planted_among_the_copies(copies):
    folder, mine, _ = copies
    validator, _ = _chat_validator(mine)

    assert validator.is_write_blocked(str(folder / "planted.txt"))[0]


def test_an_in_process_turn_keeps_every_upload_the_chat_sent(homes, monkeypatch):
    """A file sent three messages ago stays readable once the agent is rebuilt."""
    from gaia.ui import _chat_helpers
    from gaia.ui.database import ChatDatabase
    from gaia.ui.utils import uploads_dir

    folder = uploads_dir()
    folder.mkdir(parents=True)
    earlier = folder / "earlier.txt"
    earlier.write_text("sent before", encoding="utf-8")
    db = ChatDatabase(":memory:")
    session = db.create_session(title="uploads")
    db.add_message(session["id"], "user", f"see [earlier.txt]({earlier})")

    granted = _chat_helpers._chat_upload_paths(db, session["id"], [])
    validator = PathValidator(
        _chat_helpers._with_attachments(
            _chat_helpers._compute_allowed_paths([]), granted
        )
    )

    assert str(earlier) in granted
    assert validator.validate_read(str(earlier), prompt_user=False)[0]

    other = db.create_session(title="other")
    assert _chat_helpers._chat_upload_paths(db, other["id"], []) == []


def test_chat_data_under_the_default_home_is_covered_with_only_config_dir_set(
    tmp_path, monkeypatch
):
    """The Agent UI ignores GAIA_CONFIG_DIR; the rule must cover where it writes."""
    home = (tmp_path / "home").resolve()
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.delenv("GAIA_HOME", raising=False)
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path / "config"))
    upload = home / ".gaia" / "chat" / "uploads" / "x.txt"
    upload.parent.mkdir(parents=True)
    upload.write_text("x", encoding="utf-8")
    elsewhere = tmp_path / "config" / "chat" / "documents" / "y.txt"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text("y", encoding="utf-8")
    validator = PathValidator([str(home), str(tmp_path / "config")])

    assert not validator.is_path_allowed(str(upload), prompt_user=False)
    assert not validator.is_path_allowed(str(elsewhere), prompt_user=False)


def test_a_withheld_folder_that_is_gone_keeps_the_folder_advice(tmp_path):
    folder = tmp_path / "private"
    folder.mkdir()
    validator = PathValidator([str(tmp_path)])
    validator.withhold_path(str(folder))
    folder.rmdir()

    reason = validator.validate_read(str(folder / "a.txt"), prompt_user=False)[1]

    assert "Environment" in reason
    assert "attach" not in reason


def test_released_then_withheld_again_as_a_folder(tmp_path):
    """Released, a path forgets it was a file."""
    target = tmp_path / "thing"
    target.write_text("file", encoding="utf-8")
    validator = PathValidator([str(tmp_path)])
    validator.withhold_path(str(target))
    validator.release_path(str(target))
    target.unlink()
    target.mkdir()
    validator.withhold_path(str(target))

    reason = validator.validate_read(str(target / "a.txt"), prompt_user=False)[1]
    assert "Environment" in reason


def test_rows_naming_the_same_file_all_follow_it(tmp_path, homes):
    from gaia.ui.document_copies import move_library_files_out_of_shared_folder
    from gaia.ui.utils import document_copies_dir, managed_documents_dir

    db = _library_db(tmp_path)
    shared = managed_documents_dir()
    shared.mkdir(parents=True)
    first = _add(db, shared / "twice.txt", "TWICE")
    with db._transaction():
        db._conn.execute(
            "INSERT INTO documents (id, filename, filepath, file_hash) "
            "VALUES ('second', 'twice.txt', ?, 'hash-older-content')",
            (str(shared / "twice.txt"),),
        )
    second = db.get_document("second")
    assert second["filepath"] == first["filepath"]

    assert move_library_files_out_of_shared_folder(db) == []

    paths = {db.get_document(d["id"])["filepath"] for d in (first, second)}
    assert len(paths) == 1
    assert Path(paths.pop()).parent == document_copies_dir()
