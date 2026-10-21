# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A watched document another program holds open is retried, then given up on loudly.

The file watcher retries a callback that raises ``PermissionError`` (1, 2 and
4 windows apart). ``ChatAgent.reindex_file`` used to catch every exception and
only log it, so the retry never ran: a document open in Office was tried once
and never reindexed.
"""

from __future__ import annotations

import builtins
import errno
import logging
import os
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("gaia_agent_chat")

import gaia_agent_chat.agent as agent_module  # noqa: E402
from gaia_agent_chat.agent import ChatAgent  # noqa: E402

from gaia.utils.file_watcher import FileChangeHandler  # noqa: E402

AGENT_LOGGER = "gaia_agent_chat.agent"


class _Rag:
    """Stands in for RAGSDK.reindex_document; raises ``errors`` in order."""

    def __init__(self, *errors):
        self.errors = list(errors)
        self.calls = []

    def reindex_document(self, path):
        self.calls.append(path)
        if self.errors:
            raise self.errors.pop(0)
        return {"success": True}

    def remove_document(self, path):
        return True


def _agent(rag):
    agent = ChatAgent.__new__(ChatAgent)
    agent.rag = rag
    agent.indexed_files = set()
    agent.observers = []
    agent._is_path_allowed = lambda path: True
    return agent


def _sharing_violation(path, kind=PermissionError):
    """What Windows raises for a file another program has open exclusively."""
    error = kind(errno.EACCES, "being used by another process", str(path))
    error.winerror = 32
    return error


@pytest.fixture
def lock(monkeypatch):
    """Make the agent's reads of a path fail as locked, ``times`` times."""
    state = {"path": None, "left": 0, "error": PermissionError, "reads": 0}

    def _open(file, *args, **kwargs):
        if (
            isinstance(file, (str, os.PathLike))
            and os.path.realpath(file) == state["path"]
        ):
            state["reads"] += 1
            if state["left"] > 0:
                state["left"] -= 1
                raise _sharing_violation(file, state["error"])
        return builtins.open(file, *args, **kwargs)

    monkeypatch.setattr(agent_module, "open", _open, raising=False)

    def hold(path, times, error=PermissionError):
        state.update(path=os.path.realpath(path), left=times, error=error)
        return state

    return hold


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / "report.txt"
    path.write_text("final", encoding="utf-8")
    return path


def _watch(agent):
    return FileChangeHandler(
        on_created=agent.reindex_file,
        on_modified=agent.reindex_file,
        on_moved=agent._handle_file_move,
        debounce_seconds=0.05,
    )


def _created(handler, path):
    handler.on_created(SimpleNamespace(src_path=str(path), is_directory=False))
    assert handler.wait_idle(timeout=30)


def test_a_locked_document_is_retried_until_it_reindexes(doc, lock):
    state = lock(doc, times=2)
    rag = _Rag()
    agent = _agent(rag)
    handler = _watch(agent)
    try:
        _created(handler, doc)
    finally:
        handler.close()
    assert state["reads"] == 3
    assert rag.calls == [os.path.realpath(doc)]
    assert str(doc) in agent.indexed_files
    assert handler.telemetry["files_created"] == 1


def test_a_document_locked_for_good_is_given_up_on_once_loudly(doc, lock, caplog):
    state = lock(doc, times=99)
    rag = _Rag()
    agent = _agent(rag)
    handler = _watch(agent)
    try:
        with caplog.at_level(logging.WARNING):
            _created(handler, doc)
    finally:
        handler.close()
    assert state["reads"] == 4  # the first try and three retries
    assert rag.calls == []
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1, [r.getMessage() for r in errors]
    assert doc.name in errors[0].getMessage()
    assert errors[0].exc_info is not None
    assert str(doc) not in agent.indexed_files


def test_a_document_saved_by_rename_while_locked_is_retried(tmp_path, doc, lock):
    """Office saves by renaming a temp file onto the document."""
    state = lock(doc, times=1)
    rag = _Rag()
    agent = _agent(rag)
    handler = _watch(agent)
    try:
        handler.on_moved(
            SimpleNamespace(
                src_path=str(tmp_path / "~WRD0001.tmp"),
                dest_path=str(doc),
                is_directory=False,
            )
        )
        assert handler.wait_idle(timeout=30)
    finally:
        handler.close()
    assert state["reads"] == 2
    assert rag.calls == [os.path.realpath(doc)]
    assert str(doc) in agent.indexed_files


def test_a_lock_reported_as_a_plain_os_error_is_retried(doc, lock):
    state = lock(doc, times=1, error=OSError)
    rag = _Rag()
    agent = _agent(rag)
    handler = _watch(agent)
    try:
        _created(handler, doc)
    finally:
        handler.close()
    assert state["reads"] == 2
    assert str(doc) in agent.indexed_files


@pytest.mark.parametrize(
    "error",
    [
        PermissionError("Access denied: not in allowed paths"),
        PermissionError(errno.EACCES, "Permission denied", "cache.json"),
        RuntimeError("embedder exploded"),
    ],
    ids=["policy-refusal", "access-denied-inside-rag", "other"],
)
def test_a_failure_inside_rag_is_logged_with_its_traceback_not_retried(
    doc, caplog, error
):
    rag = _Rag(error)
    agent = _agent(rag)
    handler = _watch(agent)
    try:
        with caplog.at_level(logging.ERROR):
            _created(handler, doc)
    finally:
        handler.close()
    assert len(rag.calls) == 1
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r.name for r in errors] == [AGENT_LOGGER]
    assert str(os.path.realpath(doc)) in errors[0].getMessage()
    assert errors[0].exc_info is not None
    assert agent.indexed_files == set()


def test_a_document_deleted_before_it_is_read_is_skipped(doc, monkeypatch, caplog):
    def _gone(file, *args, **kwargs):
        raise FileNotFoundError(errno.ENOENT, "No such file", str(file))

    monkeypatch.setattr(agent_module, "open", _gone, raising=False)
    rag = _Rag()
    agent = _agent(rag)
    with caplog.at_level(logging.INFO, logger=AGENT_LOGGER):
        agent.reindex_file(str(doc))
    assert rag.calls == []
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("deleted before it was read" in r.getMessage() for r in caplog.records)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows mandatory locks")
def test_a_document_another_handle_has_locked_is_not_handed_to_rag(doc):
    import msvcrt

    rag = _Rag()
    agent = _agent(rag)
    size = os.path.getsize(doc)
    with open(doc, "r+b") as holder:
        msvcrt.locking(holder.fileno(), msvcrt.LK_NBLCK, size)
        try:
            with pytest.raises(PermissionError):
                agent.reindex_file(str(doc))
        finally:
            holder.seek(0)
            msvcrt.locking(holder.fileno(), msvcrt.LK_UNLCK, size)
    assert rag.calls == []
