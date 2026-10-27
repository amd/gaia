# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A running memory extraction gives the model back to the user's next turn (#5022).

The local backend serves one request at a time, so an extraction already
streaming when the user sends another message used to make that message's
first model call wait behind it — up to the 60 s extraction timeout. Now the
extraction streams, a new turn aborts it, nothing partial is stored, and the
job reruns once the user is idle.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Dict, List
from unittest.mock import MagicMock, patch

import pytest

import gaia.agents.base.memory as memory_mod
from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore
from gaia.chat.sdk import AgentConfig, AgentSDK, LLMCallCancelled

FACT = "User's favourite colour is teal"
OPS = [{"op": "add", "category": "preference", "content": FACT, "grounded": "user"}]


class _StreamingClient:
    """An LLM client that streams *chunks*, one per ``delay`` seconds."""

    def __init__(self, chunks: List[str], delay: float):
        self.chunks = chunks
        self.delay = delay
        self.closed = threading.Event()
        self.started = threading.Event()

    def chat(self, messages, model, stream, **kwargs):
        assert stream, "a cancellable call must stream"
        try:
            for chunk in self.chunks:
                self.started.set()
                time.sleep(self.delay)
                yield chunk
        finally:
            self.closed.set()

    def get_last_usage(self):
        return None

    def get_last_finish_reason(self):
        return "stop"

    def get_last_reasoning(self):
        return None


def _sdk(client) -> AgentSDK:
    with patch("gaia.chat.sdk.create_client", return_value=client):
        sdk = AgentSDK(config=AgentConfig())
    sdk.get_stats = lambda: {}
    return sdk


class TestCancellableSendMessages:
    def test_it_returns_the_whole_streamed_reply(self):
        sdk = _sdk(_StreamingClient(['[{"op"', ': "noop"}]'], delay=0))

        response = sdk.send_messages(
            [{"role": "user", "content": "hi"}], cancel=threading.Event()
        )

        assert response.text == '[{"op": "noop"}]'
        assert response.finish_reason == "stop"

    def test_cancel_closes_the_stream_and_returns_nothing(self):
        client = _StreamingClient(["chunk"] * 100, delay=0.02)
        sdk = _sdk(client)
        cancel = threading.Event()
        threading.Timer(0.1, cancel.set).start()

        with pytest.raises(LLMCallCancelled):
            sdk.send_messages([{"role": "user", "content": "hi"}], cancel=cancel)

        # Closing the stream is what makes the server stop generating.
        assert client.closed.wait(1)


class _Host(MemoryMixin):
    def __init__(self, store: MemoryStore, chat):
        self._memory_store = store
        self._memory_context = "global"
        self._memory_session_id = "s1"
        self._auto_extract_enabled = True
        self._original_user_input = None
        self.chat = chat

    def _embed_text(self, text):
        raise RuntimeError("no embedder in unit tests")

    def _hybrid_search(self, query, **kwargs):
        return []


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


@pytest.fixture(autouse=True)
def no_idle_wait(monkeypatch):
    monkeypatch.setattr(memory_mod, "EXTRACTION_IDLE_S", 0.0)


def _stored(store: MemoryStore) -> List[str]:
    return [
        r[0]
        for r in store._conn.execute(
            "SELECT content FROM knowledge WHERE source = 'llm_extract'"
        ).fetchall()
    ]


def test_a_new_turn_stops_a_running_extraction_then_it_reruns(store):
    """First call streams slowly until cancelled; the rerun answers at once."""
    calls: List[Dict] = []
    first_call_running = threading.Event()
    first_call_ended = threading.Event()

    def send_messages(messages, cancel=None, **kwargs):
        calls.append(kwargs)
        assert cancel is not None, "extraction must pass a cancel event"
        if len(calls) == 1:
            first_call_running.set()
            try:
                # A long reply arriving token by token; partial output is a
                # valid-looking op that must never be stored.
                for _ in range(600):
                    if cancel.is_set():
                        raise LLMCallCancelled("cancelled mid-reply")
                    time.sleep(0.05)
                return MagicMock(text=json.dumps(OPS), finish_reason="stop")
            finally:
                first_call_ended.set()
        return MagicMock(text=json.dumps(OPS), finish_reason="stop")

    chat = MagicMock()
    chat.send_messages.side_effect = send_messages
    host = _Host(store, chat)

    host._after_process_query(
        "Please remember that my favourite colour is teal", "Noted."
    )
    assert first_call_running.wait(5), "extraction never started"

    started = time.monotonic()
    host._note_user_turn_started()
    assert first_call_ended.wait(2), "the running extraction did not stop"
    assert time.monotonic() - started < 1.0
    # Held while the user's turn runs: nothing partial stored, job still owed.
    time.sleep(0.3)
    assert _stored(store) == []
    assert len(calls) == 1
    assert list(host._extraction_queue)[0].user_input.endswith("teal")

    host._note_user_turn_finished()
    assert host.wait_for_memory_extraction(timeout=5)

    assert len(calls) == 2
    assert _stored(store) == [FACT]


def test_a_turn_in_another_chat_stops_and_holds_a_running_extraction(tmp_path):
    """Every chat has its own agent but all share the one local model, so a
    new chat's first call waited behind the previous chat's extraction."""
    calls: List[Dict] = []
    first_call_running = threading.Event()
    first_call_ended = threading.Event()

    def send_messages(messages, cancel=None, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            first_call_running.set()
            try:
                for _ in range(600):
                    if cancel.is_set():
                        raise LLMCallCancelled("cancelled mid-reply")
                    time.sleep(0.05)
            finally:
                first_call_ended.set()
        return MagicMock(text=json.dumps(OPS), finish_reason="stop")

    chat = MagicMock()
    chat.send_messages.side_effect = send_messages
    previous_chat = _Host(MemoryStore(db_path=tmp_path / "previous.db"), chat)
    new_chat = _Host(MemoryStore(db_path=tmp_path / "new.db"), MagicMock())
    try:
        previous_chat._after_process_query(
            "Please remember that my favourite colour is teal", "Noted."
        )
        assert first_call_running.wait(5), "extraction never started"

        started = time.monotonic()
        new_chat._note_user_turn_started()
        try:
            assert first_call_ended.wait(2), "the other chat kept the model"
            assert time.monotonic() - started < 1.0
            time.sleep(0.3)
            assert len(calls) == 1, "the extraction reran during the new chat's turn"
        finally:
            new_chat._note_user_turn_finished()
        assert previous_chat.wait_for_memory_extraction(timeout=5)
        assert len(calls) == 2
        assert _stored(previous_chat._memory_store) == [FACT]
    finally:
        previous_chat._memory_store.close()
        new_chat._memory_store.close()


def test_one_chat_ending_does_not_release_while_another_chat_runs(tmp_path):
    """Overlapping turns in two chats: the extraction waits for both to end."""
    ran = threading.Event()
    chat = MagicMock()
    chat.send_messages.side_effect = lambda *a, **k: (
        ran.set(),
        MagicMock(text=json.dumps(OPS), finish_reason="stop"),
    )[-1]
    previous = _Host(MemoryStore(db_path=tmp_path / "previous.db"), chat)
    first = _Host(MemoryStore(db_path=tmp_path / "first.db"), MagicMock())
    second = _Host(MemoryStore(db_path=tmp_path / "second.db"), MagicMock())
    try:
        first._note_user_turn_started()
        second._note_user_turn_started()
        try:
            previous._after_process_query(
                "Please remember that my favourite colour is teal", "Noted."
            )
            first._note_user_turn_finished()
            assert not ran.wait(0.5), "ran while the second chat's turn was running"
        finally:
            second._note_user_turn_finished()
        assert previous.wait_for_memory_extraction(timeout=5)
        assert _stored(previous._memory_store) == [FACT]
    finally:
        for host in (previous, first, second):
            host._memory_store.close()


def test_turns_racing_across_chats_leave_no_turn_counted_and_lose_no_memory(
    tmp_path,
):
    """Turns starting and ending in many chats at once, while an extraction runs."""
    chat = MagicMock()

    def send_messages(messages, cancel=None, **kwargs):
        for _ in range(4):
            if cancel is not None and cancel.is_set():
                raise LLMCallCancelled("cancelled mid-reply")
            time.sleep(0.005)
        return MagicMock(text=json.dumps(OPS), finish_reason="stop")

    chat.send_messages.side_effect = send_messages
    previous = _Host(MemoryStore(db_path=tmp_path / "previous.db"), chat)
    chats = [
        _Host(MemoryStore(db_path=tmp_path / f"chat{i}.db"), MagicMock())
        for i in range(6)
    ]
    errors: List[BaseException] = []

    def churn(host):
        try:
            for _ in range(50):
                host._note_user_turn_started()
                try:
                    time.sleep(0.001)
                finally:
                    host._note_user_turn_finished()
        except BaseException as e:  # surfaced by the assert below
            errors.append(e)

    try:
        previous._after_process_query(
            "Please remember that my favourite colour is teal", "Noted."
        )
        threads = [threading.Thread(target=churn, args=(h,)) for h in chats]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert not errors
        assert memory_mod._USER_TURNS.in_flight == 0
        assert previous.wait_for_memory_extraction(timeout=10)
        assert _stored(previous._memory_store) == [FACT]
    finally:
        for host in [previous, *chats]:
            host._memory_store.close()


def test_each_test_starts_with_no_user_turn_and_a_cold_idle_clock():
    """The turn count is process-wide, so one test's turn must not hold the next's."""
    assert memory_mod._USER_TURNS.in_flight == 0
    assert memory_mod._USER_TURNS.last_end == float("-inf")


def test_a_flush_is_not_cancelled_by_a_new_turn(store):
    """A caller about to exit asked for the memories on disk; let it finish."""
    gate = threading.Event()

    def send_messages(messages, cancel=None, **kwargs):
        gate.wait(5)
        if cancel.is_set():
            raise LLMCallCancelled("cancelled")
        return MagicMock(text=json.dumps(OPS), finish_reason="stop")

    chat = MagicMock()
    chat.send_messages.side_effect = send_messages
    host = _Host(store, chat)

    host._after_process_query(
        "Please remember that my favourite colour is teal", "Noted."
    )
    flushed = threading.Thread(target=host.wait_for_memory_extraction, args=(5,))
    flushed.start()
    time.sleep(0.2)
    host._note_user_turn_started()
    gate.set()
    flushed.join(5)
    host._note_user_turn_finished()

    assert _stored(store) == [FACT]


def test_each_job_gets_its_own_cancel_event(store):
    """A cancelled call still in prefill keeps its set event; the rerun gets a new one."""
    events: List[threading.Event] = []
    prefill = threading.Event()
    first_started = threading.Event()

    def send_messages(messages, cancel=None, **kwargs):
        events.append(cancel)
        if len(events) == 1:
            first_started.set()
            prefill.wait(10)  # no chunk yet, so the call cannot see its event
            raise LLMCallCancelled("stopped at its first chunk")
        return MagicMock(text=json.dumps(OPS), finish_reason="stop")

    chat = MagicMock()
    chat.send_messages.side_effect = send_messages
    host = _Host(store, chat)
    host._after_process_query(
        "Please remember that my favourite colour is teal", "Noted."
    )
    assert first_started.wait(5)

    host._note_user_turn_started()
    host._note_user_turn_finished()
    assert host.wait_for_memory_extraction(timeout=5)
    prefill.set()

    assert len(events) == 2 and events[0] is not events[1]
    assert events[0].is_set(), "the abandoned call must still be told to stop"
    assert not events[1].is_set()
    assert _stored(store) == [FACT]


def test_a_timed_out_call_is_told_to_stop(store, monkeypatch):
    monkeypatch.setattr(memory_mod, "EXTRACTION_TIMEOUT_S", 0.3)
    seen: List[threading.Event] = []
    release = threading.Event()

    def send_messages(messages, cancel=None, **kwargs):
        seen.append(cancel)
        release.wait(5)
        return MagicMock(text="[]", finish_reason="stop")

    chat = MagicMock()
    chat.send_messages.side_effect = send_messages
    host = _Host(store, chat)

    assert (
        host._extract_via_llm("Please remember my favourite colour is teal", "", [])
        == []
    )
    assert seen[0].is_set()
    release.set()


def test_a_reply_that_is_all_reasoning_is_reported_as_such(store, caplog):
    """The stream wraps reasoning in <think>, unclosed when the budget ran out."""
    chat = MagicMock()
    chat.send_messages.return_value = MagicMock(
        text="<think>Let me consider what the user said about colours and",
        finish_reason="length",
    )
    host = _Host(store, chat)

    with caplog.at_level("ERROR", logger="gaia.agents.base.memory"):
        assert (
            host._extract_via_llm("Please remember my favourite colour is teal", "", [])
            == []
        )

    assert any("spent its whole" in r.getMessage() for r in caplog.records)


def test_a_stopped_extraction_is_dropped_when_the_queue_is_full(store, monkeypatch):
    """Requeued on a full queue, it would restart mid-turn; it is the oldest,
    so it is the one dropped, as a new turn on a full queue drops the oldest."""
    monkeypatch.setattr(memory_mod, "EXTRACTION_QUEUE_MAX", 1)
    first_running = threading.Event()
    prompts: List[str] = []

    def send_messages(messages, cancel=None, **kwargs):
        prompts.append(messages[0]["content"])
        if len(prompts) == 1:
            first_running.set()
            for _ in range(200):
                if cancel.is_set():
                    raise LLMCallCancelled("cancelled mid-reply")
                time.sleep(0.02)
        return MagicMock(text="[]", finish_reason="stop")

    chat = MagicMock()
    chat.send_messages.side_effect = send_messages
    host = _Host(store, chat)

    host._after_process_query(
        "Please remember that my favourite colour is teal", "Noted."
    )
    assert first_running.wait(5)
    # Queue the next turn before stopping the running job, so the queue is full
    # when the stop lands; the other order races the queue's SQLite writes.
    host._after_process_query("And I live in Calgary these days, by the way", "Okay.")
    host._note_user_turn_started()
    host._note_user_turn_finished()
    assert host.wait_for_memory_extraction(timeout=5)

    assert len(prompts) == 2
    assert "Calgary" in prompts[1]
