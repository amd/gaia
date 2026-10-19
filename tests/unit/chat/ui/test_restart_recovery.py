# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A restart, crash or shutdown never leaves a chat or a document silently stuck.

A turn's reply used to be saved only once the turn finished, and a document's
"indexing" status only changed when its in-memory task did. A backend that
went away mid-way left the question with no reply and the document indexing
forever, with nothing to say why.

"A new server" is simulated in this process by giving it a new owner token,
which is how a server tells an earlier app in its own process from itself.
"""

import asyncio
import os
import sqlite3
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import psutil
import pytest

from gaia.ui import _chat_helpers as helpers
from gaia.ui import process_owner, restart_recovery
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.restart_recovery import (
    CANCELLED_TURN_NOTICE,
    INTERRUPTED_INDEXING_ERROR,
    INTERRUPTED_TURN_NOTICE,
    recover_interrupted_work,
)
from gaia.ui.routers import chat
from gaia.ui.run_manager import RunManager

pytestmark = [pytest.mark.asyncio, pytest.mark.allow_network]


@contextmanager
def _new_server():
    with patch.object(process_owner, "_TOKEN", "the-next-server"):
        yield


class _StreamingAgent:
    """Streams part of a reply, then blocks as if the process had died there."""

    def __init__(self):
        self.model_id = "M-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self.streamed = threading.Event()
        self.release = threading.Event()

    def _register_tools(self):
        pass

    def process_query(self, _message):
        self.console.print_streaming_text("Paris is the capital of France. ")
        self.console.print_streaming_text("It is also", end_of_stream=False)
        self.streamed.set()
        self.release.wait(10)
        return "late answer"


@pytest.fixture
def runtime(tmp_path):
    db_path = str(tmp_path / "chat.db")
    db = ChatDatabase(db_path)
    manager = RunManager()
    state = SimpleNamespace(
        session_locks={}, chat_semaphore=asyncio.BoundedSemaphore(1)
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state), headers={})
    server = SimpleNamespace(
        _stream_chat_response=helpers._stream_chat_response,
        _get_chat_response=helpers._get_chat_response,
    )
    helpers._agent_cache.clear()
    with (
        patch.object(chat, "run_manager", manager),
        patch("gaia.ui.run_manager.run_manager", manager),
        patch.object(chat, "_server_mod", return_value=server),
        patch.object(helpers, "_agent_registry", None),
        patch.object(helpers, "_maybe_load_expected_model"),
        patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
        patch.object(restart_recovery, "PARTIAL_SAVE_INTERVAL", 0.0),
    ):
        yield db, db_path, manager, request
    helpers._agent_cache.clear()
    db.close()


def _seed(db, agent):
    session = db.create_session(model="M-GGUF", agent_type="chat")
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    return session["id"]


async def _start_stream(sid, http_request, db):
    response = await chat.send_message(
        ChatRequest(session_id=sid, message="What is the capital?", stream=True),
        http_request,
        db,
    )

    async def receive():
        await asyncio.Event().wait()

    async def send(_message):
        pass

    return asyncio.create_task(
        response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)
    )


async def _wait_until(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.05)


def _partial(db):
    row = db._conn.execute("SELECT partial_content FROM unfinished_turns").fetchone()
    return row[0] if row else ""


async def test_turn_cut_off_by_a_restart_says_so_after_the_restart(runtime):
    db, db_path, manager, http_request = runtime
    agent = _StreamingAgent()
    sid = _seed(db, agent)

    stream = await _start_stream(sid, http_request, db)
    try:
        assert await asyncio.to_thread(agent.streamed.wait, 5)
        # The streamed text reaches the database while the turn is still running.
        await _wait_until(lambda: "Paris" in _partial(db))

        # The process dies here. A new server opens the same database.
        restarted = ChatDatabase(db_path)
        try:
            with _new_server():
                assert recover_interrupted_work(restarted) == {
                    "turns": 1,
                    "documents": 0,
                }
            messages = restarted.get_messages(sid)
        finally:
            restarted.close()
    finally:
        agent.release.set()
        run = manager.get(sid)
        if run is not None:
            run.task.cancel()
            await asyncio.gather(run.task, return_exceptions=True)
        stream.cancel()
        await asyncio.gather(stream, return_exceptions=True)

    assert [m["role"] for m in messages] == ["user", "assistant"]
    reply = messages[-1]["content"]
    assert reply.startswith("Paris is the capital of France.")
    assert reply.endswith(INTERRUPTED_TURN_NOTICE)


async def test_finished_turn_leaves_nothing_to_recover(runtime):
    db, db_path, manager, http_request = runtime
    agent = _StreamingAgent()
    agent.release.set()
    sid = _seed(db, agent)

    stream = await _start_stream(sid, http_request, db)
    await _wait_until(lambda: len(db.get_messages(sid)) == 2)
    await _wait_until(lambda: not manager.is_running(sid))
    stream.cancel()
    await asyncio.gather(stream, return_exceptions=True)

    assert db.turns_in_progress() == []
    restarted = ChatDatabase(db_path)
    try:
        with _new_server():
            assert recover_interrupted_work(restarted) == {"turns": 0, "documents": 0}
        roles = [m["role"] for m in restarted.get_messages(sid)]
    finally:
        restarted.close()
    assert roles == ["user", "assistant"]


async def test_a_shutdown_mid_turn_keeps_the_marker_and_the_text(runtime):
    db, _db_path, manager, http_request = runtime
    agent = _StreamingAgent()
    sid = _seed(db, agent)

    stream = await _start_stream(sid, http_request, db)
    try:
        assert await asyncio.to_thread(agent.streamed.wait, 5)
        await _wait_until(lambda: manager.get(sid) is not None)
        await _wait_until(lambda: "Paris" in _partial(db))
        # Shutdown cancels the run task; the agent thread finishes later.
        run = manager.get(sid)
        run.task.cancel()
        agent.release.set()
        await asyncio.gather(run.task, return_exceptions=True)
    finally:
        agent.release.set()
        stream.cancel()
        await asyncio.gather(stream, return_exceptions=True)

    assert [t["session_id"] for t in db.turns_in_progress()] == [sid]
    with _new_server():
        assert recover_interrupted_work(db)["turns"] == 1
    reply = db.get_messages(sid)[-1]["content"]
    assert reply.startswith("Paris is the capital of France.")
    assert reply.endswith(INTERRUPTED_TURN_NOTICE)


async def test_non_streamed_turn_cut_off_says_so(runtime):
    db, db_path, _manager, http_request = runtime
    agent = _StreamingAgent()
    sid = _seed(db, agent)
    started = asyncio.Event()

    async def never_answers(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    server = SimpleNamespace(_get_chat_response=never_answers)
    with patch.object(chat, "_server_mod", return_value=server):
        task = asyncio.create_task(
            chat.send_message(
                ChatRequest(session_id=sid, message="hi", stream=False),
                http_request,
                db,
            )
        )
        await asyncio.wait_for(started.wait(), 5)
        # Shutdown, or a client that hung up: the reply will never come.
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert db.turns_in_progress() == []
    messages = db.get_messages(sid)
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == CANCELLED_TURN_NOTICE


async def test_a_non_streamed_error_leaves_no_marker(runtime):
    db, _db_path, _manager, http_request = runtime
    sid = _seed(db, _StreamingAgent())

    async def fails(*_args, **_kwargs):
        raise RuntimeError("model unreachable")

    server = SimpleNamespace(_get_chat_response=fails)
    with patch.object(chat, "_server_mod", return_value=server):
        with pytest.raises(RuntimeError):
            await chat.send_message(
                ChatRequest(session_id=sid, message="hi", stream=False),
                http_request,
                db,
            )
    assert db.turns_in_progress() == []


async def test_a_turn_that_ends_on_an_error_keeps_that_error(runtime):
    db, _db_path, _manager, _http_request = runtime
    sid = db.create_session()["id"]
    db.begin_turn(sid, "hi")

    async def fails_without_saving(*_args):
        yield 'data: {"type": "chunk", "content": "Let me check"}\n\n'
        yield 'data: {"type": "error", "content": "The model server went away."}\n\n'

    with patch.object(helpers, "_stream_chat_impl", fails_without_saving):
        run = SimpleNamespace(emit=lambda _data: None)
        request = ChatRequest(session_id=sid, message="hi")
        await helpers._run_chat_lifecycle(run, db, {}, request)

    assert db.turns_in_progress() == []
    assert db.get_messages(sid)[-1]["content"] == (
        "Let me check\n\nThe model server went away."
    )


async def test_an_unavailable_agent_keeps_its_message_without_a_retry_hint(
    runtime,
):
    db, _db_path, manager, http_request = runtime
    sid = _seed(db, _StreamingAgent())

    with patch.object(helpers, "_agent_type_unknown", return_value=True):
        stream = await _start_stream(sid, http_request, db)
        await _wait_until(lambda: len(db.get_messages(sid)) == 2)
        await _wait_until(lambda: not manager.is_running(sid))
    stream.cancel()
    await asyncio.gather(stream, return_exceptions=True)

    reply = db.get_messages(sid)[-1]["content"]
    assert reply == helpers._agent_unavailable_message("chat", None)
    assert "Send again" not in reply
    assert db.turns_in_progress() == []


async def test_a_policy_block_saved_mid_turn_is_not_taken_for_the_reply(tmp_path):
    db = ChatDatabase(str(tmp_path / "chat.db"))
    try:
        sid = db.create_session()["id"]
        question = db.begin_turn(sid, "delete my files")
        # Saved mid-turn, not as the turn's reply.
        db.upsert_message(
            sid, None, "assistant", "Blocked: rm is restricted by policy."
        )
        db.save_turn_partial(question, "I will try another way", [])
        with _new_server():
            assert recover_interrupted_work(db)["turns"] == 1
        contents = [m["content"] for m in db.get_messages(sid)]
    finally:
        db.close()
    assert contents[1] == "Blocked: rm is restricted by policy."
    assert contents[2] == f"I will try another way\n\n{INTERRUPTED_TURN_NOTICE}"


async def test_indexing_cut_off_by_a_restart_is_offered_for_retry(tmp_path):
    db_path = str(tmp_path / "chat.db")
    db = ChatDatabase(db_path)
    indexing = db.add_document("big.pdf", str(tmp_path / "big.pdf"), "h1")
    pending = db.add_document("next.pdf", str(tmp_path / "next.pdf"), "h2")
    done = db.add_document("done.pdf", str(tmp_path / "done.pdf"), "h3", chunk_count=4)
    db.update_document_status(indexing["id"], "indexing")
    db.update_document_status(pending["id"], "pending")
    db.close()

    restarted = ChatDatabase(db_path)
    try:
        with _new_server():
            assert recover_interrupted_work(restarted)["documents"] == 2
        docs = {d["id"]: d for d in restarted.list_documents()}
    finally:
        restarted.close()
    for doc_id in (indexing["id"], pending["id"]):
        # "failed" is the status the document library offers Retry for.
        assert docs[doc_id]["indexing_status"] == "failed"
        assert docs[doc_id]["last_error"] == INTERRUPTED_INDEXING_ERROR
    assert docs[done["id"]]["indexing_status"] == "complete"


async def test_app_start_runs_the_recovery(tmp_path):
    from gaia.ui.server import create_app

    db_path = str(tmp_path / "chat.db")
    db = ChatDatabase(db_path)
    sid = db.create_session()["id"]
    db.begin_turn(sid, "hello?")
    doc = db.add_document("a.pdf", str(tmp_path / "a.pdf"), "h")
    db.update_document_status(doc["id"], "indexing")
    db.close()

    with _new_server():
        app = create_app(db_path=db_path)
    try:
        messages = app.state.db.get_messages(sid)
        status = app.state.db.get_document(doc["id"])["indexing_status"]
    finally:
        app.state.db.close()
    assert messages[-1]["content"] == INTERRUPTED_TURN_NOTICE
    assert status == "failed"


async def test_work_owned_by_a_live_server_is_left_alone(tmp_path):
    db = ChatDatabase(str(tmp_path / "chat.db"))
    try:
        sid = db.create_session()["id"]
        db.begin_turn(sid, "hello?")
        doc = db.add_document("a.pdf", str(tmp_path / "a.pdf"), "h")
        db.update_document_status(doc["id"], "indexing")
        # Another live process (this test's parent) is running both.
        parent = psutil.Process(os.getppid())
        live = f"{parent.pid}:{process_owner._started(parent.pid)}:other"
        db._conn.execute("UPDATE unfinished_turns SET owner = ?", (live,))
        db._conn.execute("UPDATE documents SET indexing_owner = ?", (live,))
        db._conn.commit()

        assert recover_interrupted_work(db) == {"turns": 0, "documents": 0}

        # Once that process has exited (its pid now names a newer process).
        gone = f"{parent.pid}:1.0:other"
        db._conn.execute("UPDATE unfinished_turns SET owner = ?", (gone,))
        db._conn.execute("UPDATE documents SET indexing_owner = ?", (gone,))
        db._conn.commit()
        assert recover_interrupted_work(db) == {"turns": 1, "documents": 1}
    finally:
        db.close()


async def test_this_servers_own_work_is_never_closed(tmp_path):
    db = ChatDatabase(str(tmp_path / "chat.db"))
    try:
        sid = db.create_session()["id"]
        db.begin_turn(sid, "hello?")
        doc = db.add_document("a.pdf", str(tmp_path / "a.pdf"), "h")
        db.update_document_status(doc["id"], "indexing")
        assert recover_interrupted_work(db) == {"turns": 0, "documents": 0}
    finally:
        db.close()


async def test_a_partial_save_that_fails_does_not_end_the_turn(runtime, caplog):
    db, _db_path, _manager, _http_request = runtime
    sid = db.create_session()["id"]
    db.begin_turn(sid, "hi")
    journal = restart_recovery.TurnJournal(db, sid, interval=0.0)
    caplog.set_level("WARNING", logger=restart_recovery.logger.name)
    with patch.object(db, "save_turn_partial", side_effect=sqlite3.OperationalError):
        journal.observe('data: {"type": "chunk", "content": "Hel"}\n\n')
    assert "Could not save the streamed text" in caplog.text


async def test_recovered_text_is_cleaned_like_a_finished_reply(tmp_path):
    db = ChatDatabase(str(tmp_path / "chat.db"))
    try:
        sid = db.create_session()["id"]
        db.begin_turn(sid, "hi")
        journal = restart_recovery.TurnJournal(db, sid, interval=0.0)
        journal.observe(
            'data: {"type": "chunk", "content": '
            '"{\\"thought\\": \\"plan it\\", \\"answer\\": \\"Paris\\"}"}\n\n'
        )
        with _new_server():
            assert recover_interrupted_work(db)["turns"] == 1
        reply = db.get_messages(sid)[-1]["content"]
    finally:
        db.close()
    assert "thought" not in reply
    assert reply == f"Paris\n\n{INTERRUPTED_TURN_NOTICE}"


async def test_a_failed_close_does_not_mask_the_turns_own_error(runtime, caplog):
    db, _db_path, _manager, _http_request = runtime
    sid = db.create_session()["id"]
    db.begin_turn(sid, "hi")

    async def raises(*_args):
        yield 'data: {"type": "chunk", "content": "Let me"}\n\n'
        raise ValueError("the turn's own failure")

    caplog.set_level("WARNING", logger=helpers.logger.name)
    with (
        patch.object(helpers, "_stream_chat_impl", raises),
        patch.object(db, "close_turn", side_effect=sqlite3.OperationalError("gone")),
        pytest.raises(ValueError, match="the turn's own failure"),
    ):
        run = SimpleNamespace(emit=lambda _data: None)
        await helpers._run_chat_lifecycle(
            run, db, {}, ChatRequest(session_id=sid, message="hi")
        )
    assert "Could not close the finished turn" in caplog.text
    assert [t["session_id"] for t in db.turns_in_progress()] == [sid]


def _as_server(name):
    return patch.object(process_owner, "_TOKEN", name)


def _chunk(text):
    return f'data: {{"type": "chunk", "content": "{text}"}}\n\n'


async def test_two_servers_in_one_chat_keep_their_own_turns(tmp_path):
    """Each server's turn is its own row: A's text never lands in B's turn, and
    A's ending never erases B's marker."""
    path = str(tmp_path / "chat.db")
    a, b = ChatDatabase(path), ChatDatabase(path)
    try:
        sid = a.create_session()["id"]
        with _as_server("A"):
            a.begin_turn(sid, "question to A")
            journal_a = restart_recovery.TurnJournal(a, sid, interval=0.0)
        with _as_server("B"):
            b.begin_turn(sid, "question to B")
            journal_b = restart_recovery.TurnJournal(b, sid, interval=0.0)
            journal_b.observe(_chunk("B's partial answer"))
        with _as_server("A"):
            journal_a.observe(_chunk("A's partial answer"))
            a.add_message(sid, "assistant", "A's answer", ends_turn=True)
            journal_a.close()
        # B's process dies; a third server recovers.
        with _as_server("C"):
            assert recover_interrupted_work(a)["turns"] == 1
        contents = [m["content"] for m in a.get_messages(sid)]
    finally:
        a.close()
        b.close()
    assert contents == [
        "question to A",
        "question to B",
        "A's answer",
        f"B's partial answer\n\n{INTERRUPTED_TURN_NOTICE}",
    ]


async def test_another_servers_reply_does_not_end_this_turn(tmp_path):
    """A later reply in the chat that is not this turn's leaves its marker."""
    path = str(tmp_path / "chat.db")
    db = ChatDatabase(path)
    try:
        sid = db.create_session()["id"]
        with _as_server("B"):
            db.begin_turn(sid, "question to B")
        with _as_server("A"):
            db.add_message(sid, "assistant", "someone else's reply", ends_turn=True)
        with _as_server("C"):
            assert recover_interrupted_work(db)["turns"] == 1
        assert db.get_messages(sid)[-1]["content"] == INTERRUPTED_TURN_NOTICE
    finally:
        db.close()


async def test_a_cut_off_reply_says_which_tools_it_had_started(tmp_path):
    db = ChatDatabase(str(tmp_path / "chat.db"))
    try:
        sid = db.create_session()["id"]
        db.begin_turn(sid, "tidy my downloads")
        journal = restart_recovery.TurnJournal(db, sid, interval=0.0)
        journal.observe('data: {"type": "tool_start", "tool": "move_file"}\n\n')
        journal.observe(_chunk("Moving the PDFs"))
        with _new_server():
            assert recover_interrupted_work(db)["turns"] == 1
        reply = db.get_messages(sid)[-1]["content"]
    finally:
        db.close()
    assert reply == (
        "Moving the PDFs\n\n"
        "Before it stopped, it had started these tools: move_file.\n\n"
        f"{INTERRUPTED_TURN_NOTICE}"
    )


async def test_the_question_and_its_marker_are_saved_together(tmp_path):
    db = ChatDatabase(str(tmp_path / "chat.db"))
    try:
        sid = db.create_session()["id"]
        with patch("gaia.ui.database.current_owner", side_effect=RuntimeError):
            with pytest.raises(RuntimeError):
                db.begin_turn(sid, "hello?")
        assert db.get_messages(sid) == []
    finally:
        db.close()


async def test_the_running_server_sweeps_up_after_one_that_exited(tmp_path):
    """A turn whose server was still shutting down at our start is closed by
    the periodic sweep once that server is gone."""
    db = ChatDatabase(str(tmp_path / "chat.db"))
    try:
        sid = db.create_session()["id"]
        with _as_server("old"):
            db.begin_turn(sid, "hello?")
        gone = asyncio.Event()

        def owner_is_gone(_owner):
            return gone.is_set()

        with patch.object(restart_recovery, "owner_is_gone", owner_is_gone):
            sweep = asyncio.create_task(
                restart_recovery.sweep_interrupted_work(db, interval=0.01)
            )
            try:
                await asyncio.sleep(0.05)
                assert db.get_messages(sid)[-1]["role"] == "user"
                gone.set()  # the old server exits
                await _wait_until(lambda: db.turns_in_progress() == [])
            finally:
                sweep.cancel()
                await asyncio.gather(sweep, return_exceptions=True)
        assert db.get_messages(sid)[-1]["content"] == INTERRUPTED_TURN_NOTICE
    finally:
        db.close()


async def test_a_scheduled_run_cut_off_by_a_restart_says_so(tmp_path):
    from gaia.ui.scheduler import Scheduler

    db = ChatDatabase(str(tmp_path / "chat.db"))
    running = asyncio.Event()

    async def never_finishes(_prompt):
        running.set()
        await asyncio.Event().wait()

    sched = Scheduler(db=db, executor=never_finishes)
    await sched.start()
    try:
        await sched.create_task("digest", "every 1h", "Summarize my inbox")
        run = asyncio.create_task(sched._execute_task(sched._tasks["digest"]))
        await asyncio.wait_for(running.wait(), 5)
        run.cancel()  # shutdown mid-run
        await asyncio.gather(run, return_exceptions=True)
        sid = sched._tasks["digest"].session_id
        with _new_server():
            assert recover_interrupted_work(db)["turns"] == 1
        messages = db.get_messages(sid)
    finally:
        await sched.shutdown()
        db.close()
    assert [m["role"] for m in messages][-2:] == ["user", "assistant"]
    assert messages[-1]["content"] == INTERRUPTED_TURN_NOTICE


async def test_a_clock_step_does_not_make_a_live_server_look_gone(monkeypatch):
    """On Linux an owner is the boot id and start in ticks since boot, which an
    NTP step cannot move, unlike create_time()."""
    parent = os.getppid()
    monkeypatch.setattr(process_owner, "_LINUX", True, raising=False)
    monkeypatch.setattr(
        process_owner,
        "_linux_start",
        lambda pid: "b00t/4242" if pid == parent else None,
        raising=False,
    )
    real = psutil.Process.create_time
    # The wall clock stepped an hour: create_time() moves with it on Linux.
    monkeypatch.setattr(psutil.Process, "create_time", lambda self: real(self) + 3600)
    assert not process_owner.owner_is_gone(f"{parent}:b00t/4242:other")
    # A different process now holding that pid started at another tick.
    assert process_owner.owner_is_gone(f"{parent}:b00t/9999:other")
