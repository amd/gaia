# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The session listing the TUI's /sessions and --resume read (#5230)."""

import io
import json
import os
import sys
from pathlib import Path

import pytest
from gaia_agent import session_index, stdio
from gaia_agent.session_index import SessionIndex

WIRE = (
    Path(__file__).resolve().parents[5] / "tests" / "fixtures" / "stdio"
) / "gaia_stdio_wire.json"


def _meta(index):
    return json.loads(index.meta_path.read_text(encoding="utf-8"))


def _transcript(index):
    text = index.transcript_path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_a_turn_moves_running_then_waiting_then_done(tmp_path):
    index = SessionIndex(str(tmp_path / "abcdef01.sqlite3"), "chat")
    index.turn_started("Summarise the report\nin three bullets")
    assert _meta(index)["status"] == "running"
    assert _meta(index)["title"] == "Summarise the report"

    index.observe({"type": "needs_confirmation", "tool": "run_shell"})
    assert _meta(index)["status"] == "waiting"
    index.observe({"type": "tool_result", "tool": "run_shell"})
    assert _meta(index)["status"] == "running"

    index.observe({"type": "final", "answer": "Done."})
    meta = _meta(index)
    assert (meta["status"], meta["turns"], meta["id"]) == ("done", 1, "abcdef01")
    assert meta["pid"] == os.getpid()
    assert _transcript(index) == [
        {"user": "Summarise the report\nin three bullets", "answer": "Done."}
    ]


def test_an_error_marks_the_session_failed_and_keeps_the_question(tmp_path):
    index = SessionIndex(str(tmp_path / "5e550001.sqlite3"), "chat")
    index.turn_started("hi")
    index.observe({"type": "error", "detail": "Lemonade is not running"})
    assert _meta(index)["status"] == "failed"
    assert _transcript(index) == [{"user": "hi", "error": "Lemonade is not running"}]


def test_events_outside_a_turn_change_nothing(tmp_path):
    index = SessionIndex(str(tmp_path / "5e550001.sqlite3"), "chat")
    index.observe({"type": "final", "answer": "warmed_up"})
    assert not index.meta_path.exists()
    assert not index.transcript_path.exists()


def test_reopening_keeps_title_kind_and_age(tmp_path):
    path = str(tmp_path / "5e550001.sqlite3")
    first = SessionIndex(path, "eval")
    first.turn_started("first question")
    first.observe({"type": "final", "answer": "a"})
    created = _meta(first)["created_at"]
    first.closed()

    again = SessionIndex(path, "chat")
    again.turn_started("second question")
    again.observe({"type": "final", "answer": "b"})
    meta = _meta(again)
    assert meta["title"] == "first question"
    assert meta["kind"] == "eval"
    assert meta["created_at"] == created
    assert meta["turns"] == 2
    assert [t["user"] for t in _transcript(again)] == [
        "first question",
        "second question",
    ]


def test_an_unreadable_index_refuses_to_reopen_and_says_how_to_recover(tmp_path):
    path = tmp_path / "5e550001.sqlite3"
    path.with_suffix(".json").write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Delete it"):
        SessionIndex(str(path), "chat")


@pytest.mark.parametrize("kind", ["", "Chat", "has space", "x" * 40])
def test_a_bad_kind_is_refused(tmp_path, kind):
    with pytest.raises(ValueError, match="--session-kind"):
        SessionIndex(str(tmp_path / "5e550001.sqlite3"), kind)


def test_no_kind_means_no_index(tmp_path):
    assert session_index.open_index(str(tmp_path / "5e550001.sqlite3"), None) is None


def test_title_is_cut_to_one_short_line():
    assert session_index.title_from("\n\n  a   b \nc") == "a b"
    long = session_index.title_from("x" * 200)
    assert len(long) == session_index.TITLE_MAX and long.endswith("…")


def test_dispatch_indexes_real_turns_and_clear_empties_the_listing(
    tmp_path, monkeypatch
):
    def turn(agent, query, out, **kwargs):
        stdio._write({"type": "needs_confirmation", "tool": "write_file"}, out)
        stdio._write({"type": "final", "answer": "ok " + query}, out)

    monkeypatch.setattr(stdio, "run_turn", turn)
    monkeypatch.setattr(
        stdio,
        "run_warm_up",
        lambda agent, out: stdio._write({"type": "final", "answer": "warmed_up"}, out),
    )
    agent = type("A", (), {"conversation_history": [], "console": None})()
    index = SessionIndex(str(tmp_path / "5e550001.sqlite3"), "chat")
    wire = io.StringIO()

    stdio.dispatch_query(agent, stdio.WARM_UP_QUERY, wire, index=index)
    assert not index.meta_path.exists(), "a warm-up is not a turn"

    stdio.dispatch_query(agent, "first", wire, index=index)
    assert _meta(index)["status"] == "done"
    assert _transcript(index) == [{"user": "first", "answer": "ok first"}]
    assert '"needs_confirmation"' in wire.getvalue(), "events still reach the host"

    stdio.dispatch_query(agent, stdio.CLEAR_CONVERSATION_QUERY, wire, index=index)
    assert (_meta(index)["title"], _meta(index)["turns"]) == ("", 0)
    assert _transcript(index) == []


def _run_main(tmp_path, monkeypatch, crash=False):
    """Run stdio.main on one question with a stand-in agent; return the history path."""
    import gaia_agent.agent as agent_mod

    class Agent:
        def __init__(self, config=None):
            self.console = None
            self.conversation_history = []

        def process_query(self, query):
            return {"answer": "hello back"}

    monkeypatch.setattr(agent_mod, "GaiaAgent", Agent)
    monkeypatch.setattr(agent_mod, "GaiaAgentConfig", lambda **kwargs: None)
    monkeypatch.setattr(stdio, "_configure_logging", lambda out, dev=False: None)
    monkeypatch.setattr(stdio, "_model_state_event", lambda agent: {"type": "status"})
    monkeypatch.setattr(stdio, "_drain_memory", lambda agent: None)
    monkeypatch.setattr(
        "gaia.agents.base.history.SessionHistory.prepare", lambda *a, **k: None
    )
    if crash:

        def _crash(*_a, **_k):
            raise RuntimeError("boom outside the run loop")

        monkeypatch.setattr(stdio, "run_turn", _crash)
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello\n"))
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    class _Exit(Exception):
        pass

    def _exit(code=0):
        raise _Exit(code)

    monkeypatch.setattr(stdio, "_exit_cleanly", lambda out: _exit(0))
    history = tmp_path / "history" / "0123abcd.sqlite3"
    with pytest.raises(_Exit):
        stdio.main(["--history-file", str(history), "--session-kind", "chat"])
    return history


def test_main_writes_the_index_beside_the_history_file(tmp_path, monkeypatch):
    history = _run_main(tmp_path, monkeypatch)
    meta = json.loads(history.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["id"] == "0123abcd"
    assert (meta["title"], meta["kind"], meta["status"]) == ("hello", "chat", "done")
    assert meta["pid"] == 0, "a clean exit must not leave a live-looking pid"
    lines = history.with_suffix(".jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0]) == {"user": "hello", "answer": "hello back"}
    # The exit gave the session up: it reopens.
    SessionIndex(str(history), "chat").closed()


def test_a_crash_outside_the_turn_leaves_the_session_failed(tmp_path, monkeypatch):
    history = _run_main(tmp_path, monkeypatch, crash=True)
    meta = json.loads(history.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["status"] == "failed", "the session would read as running forever"


def test_the_index_layout_matches_the_wire_contract():
    contract = json.loads(WIRE.read_text(encoding="utf-8"))["session_index"]
    assert contract["argv"] == ["--history-file", "--session-kind"]
    assert contract["version"] == session_index.INDEX_VERSION
    assert contract["statuses"] == list(session_index.STATUSES)
    assert contract["default_kind"] == session_index.KIND_CHAT
    flags = {a.option_strings[0] for a in stdio.build_parser()._actions}
    assert set(contract["argv"]) <= flags


def test_the_index_records_every_field_the_contract_names(tmp_path):
    contract = json.loads(WIRE.read_text(encoding="utf-8"))["session_index"]
    index = SessionIndex(str(tmp_path / "5e550001.sqlite3"), "chat")
    index.turn_started("q")
    index.observe({"type": "final", "answer": "a"})
    assert sorted(_meta(index)) == sorted(contract["meta_fields"])
    assert set(_transcript(index)[0]) <= set(contract["transcript_fields"])


def test_a_reader_holding_the_index_is_waited_out(tmp_path, monkeypatch):
    real = os.replace
    calls = []

    def busy_twice(src, dst):
        calls.append(dst)
        if len(calls) <= 2:
            raise PermissionError("the file is being used by another process")
        real(src, dst)

    monkeypatch.setattr(session_index.os, "replace", busy_twice)
    monkeypatch.setattr(session_index, "REPLACE_PAUSE_S", 0)
    index = SessionIndex(str(tmp_path / "5e550001.sqlite3"), "chat")
    index.turn_started("q")
    assert _meta(index)["status"] == "running"
    assert len(calls) == 3


def test_a_listing_that_cannot_be_saved_never_swallows_the_event(tmp_path, monkeypatch):
    index = SessionIndex(str(tmp_path / "5e550001.sqlite3"), "chat")
    index.turn_started("q")

    def broken(*_a, **_k):
        raise PermissionError("denied")

    monkeypatch.setattr(session_index.os, "replace", broken)
    monkeypatch.setattr(session_index, "REPLACE_PAUSE_S", 0)
    wire = io.StringIO()
    stdio._write(
        {"type": "final", "answer": "a"}, session_index.IndexedOut(wire, index)
    )
    events = [json.loads(line) for line in wire.getvalue().splitlines()]
    assert events[-1] == {"type": "final", "answer": "a"}
    assert events[0]["type"] == "status" and "Could not save" in events[0]["message"]


def test_a_session_open_in_another_process_is_refused(tmp_path):
    path = str(tmp_path / "5e550002.sqlite3")
    held = SessionIndex(path, "chat")
    with pytest.raises(session_index.SessionOpenElsewhere, match="another GAIA window"):
        SessionIndex(path, "chat")
    held.closed()
    SessionIndex(path, "chat").closed()


def test_reopening_claims_the_session_at_once(tmp_path):
    path = tmp_path / "5e550003.sqlite3"
    path.with_suffix(".json").write_text(
        json.dumps(
            {
                "version": 1,
                "id": "5e550003",
                "title": "t",
                "kind": "chat",
                "status": "waiting",
                "created_at": 1.0,
                "updated_at": 1.0,
                "pid": 0,
                "turns": 2,
            }
        ),
        encoding="utf-8",
    )
    index = SessionIndex(str(path), "chat")
    meta = _meta(index)
    assert meta["pid"] == os.getpid(), "an idle reopened session must read as open"
    assert meta["status"] == "failed", "a turn left waiting died with its process"
    index.closed()


@pytest.mark.parametrize("name", ["notes.sqlite3", "ABCDEF01.sqlite3", "abc.sqlite3"])
def test_a_history_file_a_host_cannot_reopen_is_refused(tmp_path, name):
    with pytest.raises(ValueError, match="hex id"):
        SessionIndex(str(tmp_path / name), "chat")


def test_a_listing_failure_at_turn_start_does_not_cost_the_turn(tmp_path, monkeypatch):
    def turn(agent, query, out, **kwargs):
        stdio._write({"type": "final", "answer": "ok"}, out)

    def broken(*_a, **_k):
        raise PermissionError("denied")

    monkeypatch.setattr(stdio, "run_turn", turn)
    index = SessionIndex(str(tmp_path / "5e550004.sqlite3"), "chat")
    monkeypatch.setattr(session_index.os, "replace", broken)
    monkeypatch.setattr(session_index, "REPLACE_PAUSE_S", 0)
    agent = type("A", (), {"conversation_history": [], "console": None})()
    wire = io.StringIO()
    stdio.dispatch_query(agent, "q", wire, index=index)
    stdio.dispatch_query(agent, stdio.CLEAR_CONVERSATION_QUERY, wire, index=index)
    events = [json.loads(line) for line in wire.getvalue().splitlines()]
    assert {"type": "final", "answer": "ok"} in events
    assert events[-1] == {"type": "final", "answer": "conversation_cleared"}
    assert any("Could not save" in e.get("message", "") for e in events)
    index.closed()


def test_a_non_json_line_still_reaches_the_host(tmp_path):
    index = SessionIndex(str(tmp_path / "5e550005.sqlite3"), "chat")
    index.turn_started("q")
    wire = io.StringIO()
    session_index.IndexedOut(wire, index).write("not json\n")
    assert wire.getvalue() == "not json\n"
    assert _meta(index)["status"] == "running"
    index.closed()


def test_a_lock_held_for_an_instant_is_waited_out(tmp_path, monkeypatch):
    path = tmp_path / "5e550006.sqlite3"
    held = SessionIndex(str(path), "chat")
    calls = []
    real_sleep = session_index.time.sleep

    def release_on_first_wait(seconds):
        if not calls:
            held.closed()
        calls.append(seconds)
        real_sleep(0)

    monkeypatch.setattr(session_index.time, "sleep", release_on_first_wait)
    reopened = SessionIndex(str(path), "chat")
    assert calls, "the lock was free on the first try; the test proved nothing"
    reopened.closed()
