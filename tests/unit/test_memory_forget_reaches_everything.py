# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Forgetting reaches what the user cannot see: sensitive rows and old turns.

"Forget everything" deleted each row a filterless recall listed, but that
listing holds back sensitive rows, so a stored PIN survived while the agent
said memory was empty. The next session then rebuilt the whole profile, the
forgotten trip included, from past conversations. Runs against a real
``MemoryStore``; only the model is stubbed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.agent import TOOLS_REQUIRING_CONFIRMATION
from gaia.agents.base.memory import EMBEDDING_DIM
from gaia.agents.base.memory_store import MemoryStore
from tests.unit.test_memory_forget_sticks import _Host as _BaseHost
from tests.unit.test_memory_forget_sticks import _tool


class _Host(_BaseHost):
    _embedding_dim = EMBEDDING_DIM


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


def test_a_filterless_recall_says_what_it_held_back(store):
    _Host(store, [])
    store.store(category="fact", content="User's name is Jordan")
    store.store(category="fact", content="User's bank PIN is 4821", sensitive=True)

    out = _tool("recall")()

    assert out["count"] == 1
    assert out["sensitive_withheld"] == 1
    assert "category" in out["note"]
    assert _tool("recall")(category="fact")["count"] == 2
    assert _tool("recall")(time_from="2000-01-01")["count"] == 2


def test_forget_everything_takes_sensitive_rows_and_past_conversations(store):
    _Host(store, [])
    store.store(category="fact", content="User's name is Jordan")
    store.store(category="fact", content="User's bank PIN is 4821", sensitive=True)
    store.store_turn("s-old", "user", "I'm Jordan and my trip to Lisbon is in December")

    out = _tool("forget_everything")()

    assert out["status"] == "cleared"
    assert store._conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0] == 0
    assert store._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
    assert _tool("search_past_conversations")(query="Lisbon")["count"] == 0


def test_forget_everything_reply_gives_the_model_no_command_to_relay(store):
    """The model repeats this reply to a desktop user, who has no terminal."""
    _Host(store, [])
    message = _tool("forget_everything")()["message"]
    assert "gaia " not in message
    assert "`" not in message


def test_forget_everything_needs_the_users_approval():
    assert "forget_everything" in TOOLS_REQUIRING_CONFIRMATION


def test_a_forgotten_memory_does_not_come_back_from_old_turns(store):
    _Host(store, [])
    store.store_turn("s-old", "user", "My trip to Lisbon is December 10 to 17 with Sam")
    store.forget(
        store.store(
            category="fact", content="Trip to Lisbon December 10 to 17 with Sam"
        )
    )

    out = _tool("search_past_conversations")(query="Lisbon")

    assert out["count"] == 0
    assert out["forgotten_hidden"] == 1


def test_something_said_again_after_forgetting_is_found(store):
    _Host(store, [])
    store.forget(
        store.store(
            category="fact", content="Trip to Lisbon December 10 to 17 with Sam"
        )
    )
    # A forget in the turn's own clock tick still applies, so the user says it later.
    earlier = (datetime.now().astimezone() - timedelta(seconds=1)).isoformat()
    store._conn.execute("UPDATE forgotten SET forgotten_at = ?", (earlier,))
    store.store_turn(
        "s-new", "user", "New plan: Lisbon December 10 to 17 with Sam after all"
    )

    out = _tool("search_past_conversations")(query="Lisbon")

    assert out["count"] == 1
    assert "forgotten_hidden" not in out


def test_learning_queued_before_forget_everything_does_not_refill_memory(store):
    host = _Host(store, [])
    began = store.erase_count()
    _tool("forget_everything")()

    applied = host._execute_extraction_operations(
        [{"op": "add", "category": "fact", "content": "User's name is Jordan"}],
        existing_items=[],
        erase_count=began,
    )

    assert applied == 0
    assert store._conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0] == 0


def test_something_said_after_forget_everything_is_learned(store):
    host = _Host(store, [])
    _tool("forget_everything")()

    applied = host._execute_extraction_operations(
        [{"op": "add", "category": "fact", "content": "User's name is Jordan"}],
        existing_items=[],
        erase_count=store.erase_count(),
    )

    assert applied == 1


def _on_disk(tmp_path) -> bytes:
    return b"".join(p.read_bytes().lower() for p in tmp_path.glob("memory.db*"))


def test_forget_everything_leaves_nothing_readable_on_disk(store, tmp_path):
    _Host(store, [])
    other = MemoryStore(db_path=tmp_path / "memory.db")  # the UI's own connection
    for i in range(50):
        store.store(
            category="fact", content=f"Trip to Lisbon number {i} at QQZHOTEL{i}"
        )
        store.store_turn("s-old", "user", f"My trip to Lisbon {i} is in December " * 5)
    store.store(category="fact", content="User's bank PIN is 4821", sensitive=True)
    other.get_all_knowledge(limit=5)
    assert b"lisbon" in _on_disk(tmp_path)

    _tool("forget_everything")()

    raw = _on_disk(tmp_path)
    for gone in (b"lisbon", b"qqzhotel", b"4821"):
        assert gone not in raw, f"{gone!r} still readable in the database files"
    assert other.get_all_knowledge(limit=5)["total"] == 0
    other.close()


def test_forget_everything_says_so_when_another_reader_blocks_the_scrub(
    store, tmp_path
):
    _Host(store, [])
    store.store(category="fact", content="User's bank PIN is 4821", sensitive=True)
    reader = MemoryStore(db_path=tmp_path / "memory.db")
    reader._conn.execute("PRAGMA busy_timeout=0")
    store._conn.execute("PRAGMA busy_timeout=0")
    reader._conn.execute("BEGIN")
    reader._conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()
    # Debian's SQLite defaults secure_delete on, so compare to what it was.
    secure_delete = store._conn.execute("PRAGMA secure_delete").fetchone()[0]

    with pytest.raises(RuntimeError, match="was erased, but"):
        store.clear_all()

    reader._conn.rollback()
    reader.close()
    assert store._conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0] == 0
    assert store._conn.execute("PRAGMA secure_delete").fetchone()[0] == secure_delete


def test_a_blocked_scrub_still_drops_the_erased_facts_from_the_agent(
    store, tmp_path, monkeypatch
):
    host = _Host(store, [])
    refreshed = []
    monkeypatch.setattr(
        host, "_refresh_stable_memory_prompt", lambda: refreshed.append("prompt")
    )
    monkeypatch.setattr(host, "_rebuild_faiss_index", lambda: refreshed.append("index"))
    store.store(category="fact", content="User's bank PIN is 4821", sensitive=True)
    reader = MemoryStore(db_path=tmp_path / "memory.db")
    reader._conn.execute("PRAGMA busy_timeout=0")
    store._conn.execute("PRAGMA busy_timeout=0")
    reader._conn.execute("BEGIN")
    reader._conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()

    out = _tool("forget_everything")()

    reader._conn.rollback()
    reader.close()
    assert out["status"] == "error"
    assert "Close other GAIA windows and erase again" in out["message"]
    assert sorted(refreshed) == ["index", "prompt"]


def test_erasing_again_after_a_blocked_scrub_clears_the_file(store, tmp_path):
    _Host(store, [])
    for i in range(20):
        store.store(category="fact", content=f"Trip to Lisbon number {i}")
        store.store_turn("s-old", "user", f"My trip to Lisbon {i} is in December")
    store.store(category="fact", content="User's bank PIN is 4821", sensitive=True)
    reader = MemoryStore(db_path=tmp_path / "memory.db")
    reader._conn.execute("PRAGMA busy_timeout=0")
    store._conn.execute("PRAGMA busy_timeout=0")
    reader._conn.execute("BEGIN")
    reader._conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()

    assert _tool("forget_everything")()["status"] == "error"
    reader._conn.rollback()
    reader.close()
    retry = _tool("forget_everything")()

    assert retry["status"] == "cleared"
    raw = _on_disk(tmp_path)
    for gone in (b"lisbon", b"4821"):
        assert gone not in raw, f"{gone!r} still readable after erasing again"


class _AgentBase:
    """The agent loop: runs *during_turn* in place of the model's tool calls."""

    during_turn = None

    def process_query(self, user_input, **kwargs):
        if self.during_turn is not None:
            self.during_turn()
        return self.get_memory_system_prompt()

    def _execute_tool(self, tool_name, tool_args):
        if tool_args.get("path") == "missing.txt":
            return {"status": "error", "error": "File not found: missing.txt"}
        if tool_args.get("command") == "pytest -x":
            return {"status": "error", "error": "unrecognized arguments: -x"}
        return {"status": "success", "result": "done"}

    def rebuild_system_prompt(self):
        pass


class _Chat(_Host, _AgentBase):
    """A chat agent: a turn runs the memory hooks, then reads its prompt."""


def _open_chat(store, session_id):
    chat = _Chat(store, [])
    chat._memory_session_id = session_id
    chat._auto_extract_enabled = False
    return chat


def _turn(chat, user_input, reply="Done."):
    prompt = chat.process_query(user_input)
    chat._after_process_query(user_input, reply)
    return prompt


def _erase_from_memory_page(tmp_path):
    """Erase the way the Memory page does: its own connection to the file."""
    page = MemoryStore(db_path=tmp_path / "memory.db")
    try:
        page.clear_all()
    finally:
        page.close()


def _rows(store, table, where=""):
    return store._conn.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0]


def test_another_open_chat_stops_showing_what_forget_everything_erased(store):
    store.store(category="fact", content="User's trip to Lisbon is in December")
    other = _open_chat(store, "s-other")
    assert "Lisbon" in other.process_query("hi")
    _open_chat(store, "s-erasing")  # the tool is bound to the chat built last

    assert _tool("forget_everything")()["status"] == "cleared"

    assert "Lisbon" not in other.process_query("what do you know about me?")


def test_an_open_chat_stops_showing_what_the_memory_page_erased(
    store, tmp_path, monkeypatch
):
    from gaia.ui.routers import memory as memory_router

    store.store(category="fact", content="User's trip to Lisbon is in December")
    chat = _open_chat(store, "s-open")
    assert "Lisbon" in chat.process_query("hi")
    page_store = MemoryStore(db_path=tmp_path / "memory.db")
    monkeypatch.setattr(memory_router, "_get_store", lambda: page_store)

    memory_router.clear_all_memory()
    page_store.close()

    assert "Lisbon" not in chat.process_query("what do you know about me?")


def test_a_chat_with_nothing_erased_keeps_its_frozen_prompt(store):
    store.store(category="fact", content="User's trip to Lisbon is in December")
    chat = _open_chat(store, "s-open")
    first = chat.process_query("hi")
    store.store(category="fact", content="User's dog is called Rex")

    assert chat.process_query("hello again") == first


def test_an_erase_during_a_turn_keeps_the_turn_out_of_memory(store, tmp_path):
    chat = _open_chat(store, "s-busy")

    def tools_then_erase_then_tools():
        chat._execute_tool("run_shell_command", {"command": "pytest -x"})
        _erase_from_memory_page(tmp_path)
        # A fix (a lesson), a new failure (an error memory) and a plain call.
        chat._execute_tool("run_shell_command", {"command": "pytest -q"})
        chat._execute_tool("read_file", {"path": "missing.txt"})
        chat._execute_tool("search_web", {"query": "Lisbon flights December"})

    chat.during_turn = tools_then_erase_then_tools
    _turn(chat, "Book my trip to Lisbon in December", "I found three flights.")

    assert _rows(store, "knowledge") == 0
    assert _rows(store, "conversations") == 0
    assert _rows(store, "tool_history") == 0


def test_an_erase_while_the_turn_starts_up_keeps_it_out_of_memory(store, tmp_path):
    store.store(category="fact", content="User's trip to Lisbon is in December")
    chat = _open_chat(store, "s-starting")
    assert "Lisbon" in chat.process_query("hi")
    chat._memory_post_init_pending = True
    chat._run_memory_post_init = lambda: _erase_from_memory_page(tmp_path)

    prompt = _turn(chat, "When is my Lisbon trip?", "It is in December.")

    assert "Lisbon" not in prompt
    assert _rows(store, "conversations") == 0


def test_a_turn_begun_after_forget_everything_is_logged(store):
    chat = _open_chat(store, "s-new")
    _tool("forget_everything")()
    chat.during_turn = lambda: chat._execute_tool("search_web", {"query": "Porto"})

    _turn(chat, "Plan a trip to Porto", "Here is a Porto plan.")

    assert _rows(store, "conversations") == 2
    assert _rows(store, "tool_history") == 1


def test_an_erase_between_two_extracted_memories_stops_the_second(
    store, tmp_path, monkeypatch
):
    host = _Host(store, [])
    began = store.erase_count()
    monkeypatch.setattr(
        host,
        "_embed_and_index",
        lambda *args: _erase_from_memory_page(tmp_path),
    )

    applied = host._execute_extraction_operations(
        [
            {"op": "add", "category": "fact", "content": "User's name is Jordan"},
            {"op": "add", "category": "fact", "content": "Jordan lives in Lisbon"},
        ],
        existing_items=[],
        erase_count=began,
    )

    assert applied == 1
    assert _rows(store, "knowledge") == 0


def test_an_erase_while_an_old_chat_is_summarised_stores_nothing(store, tmp_path):
    host = _Host(store, [])
    for i in range(6):
        store.store_turn("s-old", "user", f"My trip to Lisbon with Sam, part {i}")
    old = (datetime.now().astimezone() - timedelta(days=30)).isoformat()
    store._conn.execute("UPDATE conversations SET timestamp = ?", (old,))
    store._conn.commit()
    reply = (
        '{"summary": "User planned a Lisbon trip with Sam.", "knowledge": '
        '[{"category": "fact", "content": "User is travelling to Lisbon with Sam"}]}'
    )

    def summarise(messages, **kwargs):
        _erase_from_memory_page(tmp_path)
        return MagicMock(text=reply, finish_reason="stop")

    host.chat.send_messages.side_effect = summarise

    result = host.consolidate_old_sessions()

    assert result["extracted_items"] == 0
    assert _rows(store, "knowledge") == 0


def test_forget_everything_erases_the_goals_skill_learning_kept(store, tmp_path):
    _Host(store, [])
    store.mark_sessions_synthesized(
        ["s-trip"],
        outcome="unusable",
        goal="Plan my Lisbon trip with Sam",
        detail="Lisbon plan had one step",
    )

    _tool("forget_everything")()

    marks = store.get_synthesis_marks(["s-trip"])
    assert marks["s-trip"]["outcome"] == "unusable"
    assert marks["s-trip"]["goal"] is None
    assert b"lisbon" not in _on_disk(tmp_path)


def test_forget_everything_leaves_deleted_chats_deleted(store):
    _Host(store, [])
    store.delete_session("s-deleted")

    _tool("forget_everything")()
    store.store_turn("s-deleted", "user", "a late turn from a deleted chat")

    assert _rows(store, "deleted_sessions") == 1
    assert _rows(store, "conversations") == 0
