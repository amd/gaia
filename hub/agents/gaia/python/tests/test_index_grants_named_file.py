# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/index`` grants the file it is given, and ``/resume`` only lists what indexed.

``RAGSDK.index_document`` refuses a document it cannot read for the cache key,
and without a grant the SDK only reads the working directory. So a path the
user names outside it has to be granted first, as ``_index_documents`` does,
and a document that is refused on resume must not be reported as loaded.
"""

import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from gaia_agent.chat_agent import ChatAgent
from gaia_agent.interactive import interactive_mode

from gaia.security import PathValidator


class _FakeRag:
    """Refuses a path outside the validator's scope, as the real SDK does."""

    def __init__(self, validator):
        self.validator = validator
        self.indexed_files = set()

    def index_document(self, path):
        if not self.validator.is_path_allowed(path):
            return {
                "success": False,
                "error": "file_unreadable",
                "error_type": "file_unreadable",
            }
        self.indexed_files.add(path)
        return {"success": True, "num_chunks": 1, "total_indexed_files": 1}


@pytest.fixture
def outside(tmp_path, monkeypatch):
    """A readable file outside the working folder, and the working folder."""
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    (folder / "report.pdf").write_bytes(b"%PDF-1.4\n")
    return folder


def _agent(validator, **extra):
    return SimpleNamespace(
        path_validator=validator,
        rag=_FakeRag(validator),
        indexed_files=set(),
        current_session=None,
        conversation_history=[],
        chat_history=[],
        save_current_session=Mock(return_value=True),
        session_manager=Mock(),
        tool_loader=Mock(),
        rebuild_system_prompt=Mock(),
        **extra,
    )


def _run(agent, monkeypatch, *lines):
    monkeypatch.setattr("builtins.input", Mock(side_effect=[*lines, "/quit", EOFError]))
    interactive_mode(agent)


def test_index_grants_a_named_file_outside_the_working_folder(
    outside, monkeypatch, capsys
):
    pdf = outside / "report.pdf"
    agent = _agent(PathValidator())
    assert not agent.path_validator.is_path_allowed(str(pdf))

    _run(agent, monkeypatch, f"/index {pdf}")

    assert str(pdf.absolute()) in agent.rag.indexed_files
    assert "INDEXING SUCCESSFUL" in capsys.readouterr().out


def test_index_grants_the_file_only_not_its_folder(outside, monkeypatch):
    pdf = outside / "report.pdf"
    neighbour = outside / "other.pdf"
    neighbour.write_bytes(b"%PDF-1.4\n")
    agent = _agent(PathValidator())

    _run(agent, monkeypatch, f"/index {pdf}")

    assert agent.path_validator.is_path_allowed(str(pdf))
    assert not agent.path_validator.is_path_allowed(str(neighbour))


def test_index_of_a_folder_grants_the_files_it_finds(outside, monkeypatch):
    agent = _agent(PathValidator())

    _run(agent, monkeypatch, f"/index {outside}")

    assert str((outside / "report.pdf").absolute()) in agent.rag.indexed_files


def _resume(tmp_path, validator, docs):
    """``ChatAgent.load_session`` on a stub that holds the given session."""
    session = SimpleNamespace(
        indexed_documents=[str(d) for d in docs],
        watched_directories=[],
        chat_history=[],
    )
    stub = SimpleNamespace(
        session_manager=SimpleNamespace(load_session=lambda sid: session),
        rag=_FakeRag(validator),
        indexed_files=set(),
        watch_directories=[],
        current_session=None,
        conversation_history=[],
    )
    return stub, session


def test_resume_does_not_list_a_document_that_was_refused(outside, tmp_path):
    pdf = outside / "report.pdf"
    stub, _ = _resume(tmp_path, PathValidator(), [pdf])

    assert ChatAgent.load_session(stub, "s1") is True

    assert stub.indexed_files == set()


def test_resume_lists_a_document_that_indexed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    doc = tmp_path / "notes.pdf"
    doc.write_bytes(b"%PDF-1.4\n")
    stub, _ = _resume(tmp_path, PathValidator(), [doc])

    assert ChatAgent.load_session(stub, "s1") is True

    assert stub.indexed_files == {str(doc)}


def test_resume_logs_why_a_document_was_not_restored(outside, tmp_path, caplog):
    pdf = outside / "report.pdf"
    stub, _ = _resume(tmp_path, PathValidator(), [pdf])

    with caplog.at_level(logging.WARNING):
        ChatAgent.load_session(stub, "s1")

    assert "report.pdf" in caplog.text
    assert "file_unreadable" in caplog.text


def test_resume_does_not_grant_a_path_the_session_file_names(outside, tmp_path):
    """A session file is data on disk: restoring it must not widen the scope."""
    pdf = outside / "report.pdf"
    validator = PathValidator()
    stub, _ = _resume(tmp_path, validator, [pdf])

    ChatAgent.load_session(stub, "s1")

    assert not validator.is_path_allowed(str(pdf))


def test_resume_reports_how_many_documents_are_loaded(
    outside, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    ok = tmp_path / "notes.pdf"
    ok.write_bytes(b"%PDF-1.4\n")
    pdf = outside / "report.pdf"
    validator = PathValidator()
    agent = _agent(validator)
    session = SimpleNamespace(
        indexed_documents=[str(ok), str(pdf)],
        watched_directories=[],
        chat_history=[],
    )

    def load(_sid):
        agent.indexed_files = {str(ok)}
        agent.current_session = session
        return True

    agent.load_session = load

    _run(agent, monkeypatch, "/resume s1")

    assert "1 of 2 documents" in capsys.readouterr().out
