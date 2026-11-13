# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A document the agent indexes on request lands in the library and the chat.

"Add X to my documents" ran ``index_document`` in the agent (a sidecar), but
the Agent UI's Document library stayed empty and a new chat found nothing.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.ui._chat_helpers import _add_indexed_documents_to_library, _done_event
from gaia.ui.agent_documents import add_indexed_documents, not_yet_indexed
from gaia.ui.database import ChatDatabase
from gaia.ui.email_sidecar.relay import _dispatch_one
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator
from gaia.ui.turn_summary import TurnLedger
from gaia.ui.utils import message_to_response


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A user home as the only document root, with GAIA's own folder outside it."""
    user_home = tmp_path / "home"
    user_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.delenv("GAIA_DOCUMENT_ROOTS", raising=False)
    return user_home


@pytest.fixture
def db():
    database = ChatDatabase(":memory:")
    yield database
    database.close()


class _RagHost(RAGToolsMixin):
    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set()
        self.current_session = None
        self.max_chunks = 5
        self.rebuild_system_prompt = MagicMock()


def _index_with_tool(path: Path) -> dict:
    rag = MagicMock()
    rag.index_document.return_value = {
        "success": True,
        "file_name": path.name,
        "num_chunks": 3,
    }
    _RagHost(rag).register_rag_tools()
    return _TOOL_REGISTRY["index_document"]["function"](file_path=str(path))


def _relayed_ledger(tool: str, args: dict, result: dict) -> TurnLedger:
    """The UI's ledger after *result* crossed the sidecar relay."""
    sidecar = SSEOutputHandler()
    sidecar.print_tool_usage(tool)
    sidecar.pretty_print_json(args, "Arguments")
    sidecar.pretty_print_json(result, "Result")
    translator = CanonicalTranslator(run_id=None, agent_id="gaia")
    wire = []
    while not sidecar.event_queue.empty():
        event = sidecar.event_queue.get_nowait()
        if event is not None:
            wire.extend(translator.translate(event))
    ui = SSEOutputHandler()
    for event in wire:
        _dispatch_one(ui, event)
    return ui.turn_ledger


def test_index_document_names_the_file_it_indexed(home):
    doc = home / "plant_manual.md"
    doc.write_text("Backup generator code: BG-7731", encoding="utf-8")

    result = _index_with_tool(doc)

    assert result["status"] == "success"
    assert Path(result["file_path"]) == doc.resolve()
    assert result["num_chunks"] == 3


def test_a_relayed_index_reaches_the_ui_with_its_path_and_chunks(home):
    doc = home / "plant_manual.md"
    doc.write_text("Backup generator code: BG-7731", encoding="utf-8")
    result = _index_with_tool(doc)

    ledger = _relayed_ledger("index_document", {"file_path": str(doc)}, result)

    assert ledger.indexed_documents() == [(result["file_path"], 3)]


def test_failed_or_unfinished_indexing_is_not_recorded(home):
    ledger = TurnLedger()
    for data, success in (
        ({"status": "error", "file_path": str(home / "a.md")}, False),
        ({"status": "in_progress", "file_name": "b.pdf"}, True),
    ):
        ledger.tool_started("index_document")
        ledger.tool_args({"file_path": "x"})
        ledger.tool_result(data, success)

    assert ledger.indexed_documents() == []


def test_an_indexed_file_is_in_the_library_and_attached_to_the_chat(home, db):
    doc = home / "plant_manual.md"
    doc.write_text("Backup generator code: BG-7731", encoding="utf-8")
    session = db.create_session()

    added = add_indexed_documents(db, session["id"], [(str(doc), 3)])

    assert added.notices == []
    [library_doc] = db.list_documents()
    assert Path(library_doc["filepath"]) == doc.resolve()
    assert library_doc["chunk_count"] == 3
    assert [d["id"] for d in db.get_session_documents(session["id"])] == [
        library_doc["id"]
    ]
    assert added.documents[0]["sessions_using"] == 1


def test_indexing_the_same_file_twice_keeps_one_library_entry(home, db):
    doc = home / "plant_manual.md"
    doc.write_text("Backup generator code: BG-7731", encoding="utf-8")
    first, second = db.create_session(), db.create_session()

    add_indexed_documents(db, first["id"], [(str(doc), 3)])
    add_indexed_documents(db, second["id"], [(str(doc), 3)])

    [library_doc] = db.list_documents()
    assert library_doc["sessions_using"] == 2


def test_a_file_outside_the_library_folders_goes_in_as_a_copy(tmp_path, home, db):
    outside = tmp_path / "gtest" / "plant_manual.md"
    outside.parent.mkdir()
    outside.write_text("Backup generator code: BG-7731", encoding="utf-8")
    session = db.create_session()

    added = add_indexed_documents(db, session["id"], [(str(outside), 3)])

    [library_doc] = db.list_documents()
    copy = Path(library_doc["filepath"])
    assert copy != outside.resolve()
    assert copy.read_text(encoding="utf-8") == "Backup generator code: BG-7731"
    assert library_doc["filename"] == "plant_manual.md"
    assert db.get_session_documents(session["id"])[0]["id"] == library_doc["id"]
    [notice] = added.notices
    assert "copy" in notice and str(outside.resolve()) in notice


def _outside(tmp_path: Path, text: str = "Backup generator code: BG-7731") -> Path:
    outside = tmp_path / "gtest" / "plant_manual.md"
    outside.parent.mkdir(exist_ok=True)
    outside.write_text(text, encoding="utf-8")
    return outside


def test_reindexing_a_copied_file_does_not_repeat_the_copy_notice(tmp_path, home, db):
    outside = _outside(tmp_path)
    session = db.create_session()

    first = add_indexed_documents(db, session["id"], [(str(outside), 3)])
    again = add_indexed_documents(db, session["id"], [(str(outside), 3)])

    assert len(first.notices) == 1
    assert again.notices == []
    assert len(db.list_documents()) == 1
    assert [d["id"] for d in again.documents] == [d["id"] for d in first.documents]


def test_a_content_match_elsewhere_is_named_instead_of_copied(tmp_path, home, db):
    inside = home / "Documents" / "b2" / "plant_manual.md"
    inside.parent.mkdir(parents=True)
    inside.write_text("Backup generator code: BG-7731", encoding="utf-8")
    outside = _outside(tmp_path)
    session = db.create_session()
    add_indexed_documents(db, session["id"], [(str(inside), 3)])

    added = add_indexed_documents(db, session["id"], [(str(outside), 3)])

    [library_doc] = db.list_documents()
    assert Path(library_doc["filepath"]) == inside.resolve()
    [notice] = added.notices
    assert "same content" in notice and str(inside.resolve()) in notice
    assert "copy" not in notice


def test_two_library_files_with_the_same_content_are_named(home, db):
    first = home / "b2" / "harbor.md"
    first.parent.mkdir()
    first.write_text("Passphrase: tidewater", encoding="utf-8")
    second = home / "harbor.md"
    second.write_text("Passphrase: tidewater", encoding="utf-8")
    session = db.create_session()
    add_indexed_documents(db, session["id"], [(str(first), 3)])

    added = add_indexed_documents(db, session["id"], [(str(second), 3)])

    [notice] = added.notices
    assert str(first.resolve()) in notice and "same content" in notice


def test_the_library_copy_of_an_indexed_file_is_not_indexed_again(tmp_path, home, db):
    """The agent indexed the original; next turn the chat lists the copy too."""
    outside = _outside(tmp_path)
    session = db.create_session()
    add_indexed_documents(db, session["id"], [(str(outside), 3)])
    copy = db.get_session_documents(session["id"])[0]["filepath"]
    other = home / "other.md"
    other.write_text("Something else", encoding="utf-8")

    assert not_yet_indexed([copy, str(other)], {str(outside)}) == [str(other)]


def test_a_changed_or_unreadable_file_is_still_indexed(tmp_path, home):
    outside = _outside(tmp_path)
    edited = home / "plant_manual.md"
    edited.write_text("Backup generator code: BG-9000", encoding="utf-8")
    missing = str(home / "gone.md")

    assert not_yet_indexed([str(edited), missing], {str(outside)}) == [
        str(edited),
        missing,
    ]
    assert not_yet_indexed([str(outside)], {str(outside)}) == []


def test_a_file_the_library_cannot_hold_is_named_with_the_reason(home, db):
    script = home / "run.exe"
    script.write_bytes(b"MZ")
    session = db.create_session()

    added = add_indexed_documents(db, session["id"], [(str(script), 1)])

    assert db.list_documents() == []
    [notice] = added.notices
    assert "run.exe" in notice and "couldn't be added to your documents" in notice
    assert ".exe" in notice


def test_library_notices_travel_on_done_events_and_persisted_messages(home, db):
    session = db.create_session()
    notices = [
        "run.exe couldn't be added to your documents (.exe files are not supported)."
    ]
    msg_id = db.add_message(
        session["id"],
        "assistant",
        "I indexed the remaining files.",
        library_notices=notices,
    )

    [saved] = db.get_messages(session["id"])
    assert saved["library_notices"] == notices
    assert (
        _done_event(1, "ok", False, library_notices=notices)["library_notices"]
        == notices
    )
    assert message_to_response(saved).library_notices == notices
    assert msg_id == saved["id"]


def test_the_turn_hands_the_chat_its_new_documents(home, db):
    doc = home / "plant_manual.md"
    doc.write_text("Backup generator code: BG-7731", encoding="utf-8")
    result = _index_with_tool(doc)
    handler = MagicMock()
    handler.turn_ledger = _relayed_ledger(
        "index_document", {"file_path": str(doc)}, result
    )
    session = db.create_session()

    documents, notices = asyncio.run(
        _add_indexed_documents_to_library(db, session["id"], handler)
    )

    assert notices == []
    assert [d["filename"] for d in documents] == ["plant_manual.md"]
    done = _done_event(1, "BG-7731", False, documents_added=documents)
    assert json.loads(json.dumps(done))["documents_added"] == documents


def test_a_turn_that_indexed_nothing_adds_nothing(db):
    handler = MagicMock()
    handler.turn_ledger = TurnLedger()

    assert asyncio.run(_add_indexed_documents_to_library(db, "s", handler)) == (
        [],
        [],
    )
    assert "documents_added" not in _done_event(1, "ok", False)
