# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A new chat reuses the memory section other chats of this process render.

The chat template puts every tool schema after the system text, so a memory
section that changed between two chats made the second one re-read the whole
~19K-token prompt (~45 s on Gemma-4-E4B). What a chat learns still reaches
later chats through per-turn recall; an explicit edit re-renders at once.
"""

from __future__ import annotations

import pytest

from gaia.agents.base import memory as memory_mod
from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore


class _Host(MemoryMixin):
    def __init__(self, store: MemoryStore, context: str = "global"):
        self._memory_store = store
        self._memory_context = context


@pytest.fixture(autouse=True)
def _clean_shared(monkeypatch):
    monkeypatch.setattr(memory_mod, "_SHARED_STABLE_PROMPTS", {})


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "memory.db")
    s.store("fact", "The user's team ships GPU drivers.", source="user")
    yield s
    s.close()


def _new_chat(store, context="global"):
    host = _Host(store, context)
    host._refresh_stable_memory_prompt(share=True)
    return host


def test_a_new_chat_keeps_the_section_after_memory_changed(store):
    first = _new_chat(store).get_memory_system_prompt()
    store.store("preference", "Prefers metric units in every answer.", source="user")
    assert _new_chat(store).get_memory_system_prompt() == first


def test_an_explicit_change_re_renders_for_later_chats(store):
    first = _new_chat(store).get_memory_system_prompt()
    store.store("preference", "Prefers metric units in every answer.", source="user")
    _Host(store)._refresh_stable_memory_prompt()  # an edit, a reset, a switch
    later = _new_chat(store).get_memory_system_prompt()
    assert later != first and "metric units" in later


def test_the_shared_section_expires(store, monkeypatch):
    first = _new_chat(store).get_memory_system_prompt()
    store.store("preference", "Prefers metric units in every answer.", source="user")
    monkeypatch.setattr(memory_mod, "SHARED_STABLE_TTL_S", 0.0)
    assert _new_chat(store).get_memory_system_prompt() != first


def test_a_shared_section_carries_which_items_it_shows(store):
    """Editing an item a chat shows must still re-render that chat."""
    rendered = _new_chat(store)
    rendered.get_memory_system_prompt()
    reused = _new_chat(store)
    reused.get_memory_system_prompt()
    assert reused._stable_memory_ids == rendered._stable_memory_ids
    assert reused._stable_memory_ids


def test_contexts_do_not_share(store):
    work = _new_chat(store, "work").get_memory_system_prompt()
    store.store(
        "fact", "Home project is a garden planner.", source="user", context="home"
    )
    home = _new_chat(store, "home").get_memory_system_prompt()
    assert "garden planner" in home and "garden planner" not in work


def test_a_memory_erased_since_is_not_shown_to_a_new_chat(store):
    """Privacy: an erase anywhere must reach the next chat, shared copy or not."""
    secret = store.store("fact", "The user's door code is 4417.", source="user")
    first = _new_chat(store).get_memory_system_prompt()
    assert "4417" in first
    store.forget(secret)
    assert "4417" not in _new_chat(store).get_memory_system_prompt()


def test_each_chat_model_gets_its_own_section(store):
    """Tool errors are shown per model, so models must not share a section."""
    a = _Host(store)
    a.model_id = "model-a"
    a._refresh_stable_memory_prompt(share=True)
    a.get_memory_system_prompt()
    b = _Host(store)
    b.model_id = "model-b"
    b._refresh_stable_memory_prompt(share=True)
    b.get_memory_system_prompt()
    assert len(memory_mod._SHARED_STABLE_PROMPTS) == 2


_WORDS = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
    "mike november oscar papa quebec romeo sierra tango uniform victor whiskey "
    "xray yankee zulu"
).split()


def test_a_memory_pushed_out_by_the_size_cap_still_shares(store):
    """New facts displace older shown ones; that is not a removal."""
    for n in range(30):
        store.store(
            "fact",
            f"The {_WORDS[n % 26]} {n} project uses {_WORDS[(n * 7) % 26]} widgets.",
            source="user",
        )
    first = _new_chat(store).get_memory_system_prompt()
    store.store("fact", "The zebra crossing project uses granite slabs.", source="user")
    fresh = _Host(store)._build_stable_memory_prompt()
    assert set(first.splitlines()) - set(fresh.splitlines())  # lines were displaced
    assert _new_chat(store).get_memory_system_prompt() == first


def test_an_edited_memory_is_not_shown_from_the_shared_copy(store):
    item = store.store("fact", "The user's office is in building 7.", source="user")
    _new_chat(store).get_memory_system_prompt()
    store.update(item, content="The user's office is in building 9.")
    later = _new_chat(store).get_memory_system_prompt()
    assert "building 9" in later and "building 7" not in later


def test_a_memory_marked_sensitive_is_not_shown_to_a_new_chat(store):
    """The Memory page can hide a memory without changing its text."""
    secret = store.store("fact", "The user's door code is 4417.", source="user")
    assert "4417" in _new_chat(store).get_memory_system_prompt()
    store.update(secret, sensitive=True)
    assert "4417" not in _new_chat(store).get_memory_system_prompt()


def test_a_memory_moved_to_another_context_is_not_shown(store):
    """A "home" chat must stop showing a memory moved to "work"."""
    moved = store.store(
        "fact", "The user's bank is Northwind.", source="user", context="home"
    )
    assert "Northwind" in _new_chat(store, "home").get_memory_system_prompt()
    store.update(moved, context="work")
    assert "Northwind" not in _new_chat(store, "home").get_memory_system_prompt()


def _past_due():
    from datetime import datetime, timedelta

    return (datetime.now().astimezone() - timedelta(days=30)).isoformat()


def _two_notes(store):
    """The kept note holds the "Notes:" header, so only the edited line drops."""
    store.store("note", "Water the plants on Fridays.", source="user")
    return store.store("note", "Renew the passport 4417.", source="user")


def test_a_due_date_edited_since_is_not_shown_from_the_shared_copy(store):
    """A due-date edit hides a note without touching its text."""
    note = _two_notes(store)
    assert "4417" in _new_chat(store).get_memory_system_prompt()
    store.update(note, due_at=_past_due())
    assert "4417" not in _new_chat(store).get_memory_system_prompt()


def test_an_edit_landing_during_the_render_is_not_hidden(store, monkeypatch):
    """The render time is taken before rendering, so a concurrent edit counts."""
    note = _two_notes(store)
    build = MemoryMixin._build_stable_memory_prompt

    def build_then_edit(self):
        text = build(self)
        monkeypatch.setattr(MemoryMixin, "_build_stable_memory_prompt", build)
        store.update(note, due_at=_past_due())
        return text

    monkeypatch.setattr(MemoryMixin, "_build_stable_memory_prompt", build_then_edit)
    assert "4417" in _new_chat(store).get_memory_system_prompt()
    assert "4417" not in _new_chat(store).get_memory_system_prompt()


@pytest.mark.parametrize("stamp", ["2020-01-01T00:00:00", "not a date", ""])
def test_an_unreadable_update_stamp_renders_fresh(store, stamp):
    """A legacy offset-less or garbled stamp can't be ordered; never crash on it."""
    note = _two_notes(store)
    assert "4417" in _new_chat(store).get_memory_system_prompt()
    store.update(note, due_at=_past_due())
    store._conn.execute(
        "UPDATE knowledge SET updated_at = ? WHERE id = ?", (stamp, note)
    )
    store._conn.commit()
    assert "4417" not in _new_chat(store).get_memory_system_prompt()


def _thirty_facts(store):
    for n in range(30):
        store.store(
            "fact",
            f"The {_WORDS[n % 26]} {n} project uses {_WORDS[(n * 7) % 26]} widgets.",
            source="user",
        )


def _pushed_out_by_next_store(store):
    """The content of the shown fact the next store pushes out of the section."""
    before = _Host(store)
    before._build_stable_memory_prompt()
    shown = set(before._stable_memory_ids)
    store.store("fact", "The zebra crossing project uses granite slabs.", source="user")
    after = _Host(store)
    after._build_stable_memory_prompt()
    dropped = shown - after._stable_memory_ids
    assert dropped
    return store.get_item(sorted(dropped)[0])["content"]


def test_an_edit_hidden_behind_a_concurrent_cap_drop_still_shows(tmp_path):
    """Verifier's reproducer: a dropped fact's text inside the edited memory's old line."""
    probe = MemoryStore(tmp_path / "probe.db")
    probe.store("fact", "The user's team ships GPU drivers.", source="user")
    _thirty_facts(probe)
    probe.store("fact", "Door code 4417. placeholder", source="user")
    dropped = _pushed_out_by_next_store(probe)
    probe.close()

    store = MemoryStore(tmp_path / "memory.db")
    store.store("fact", "The user's team ships GPU drivers.", source="user")
    _thirty_facts(store)
    secret = store.store("fact", f"Door code 4417. {dropped}", source="user")
    first = _new_chat(store).get_memory_system_prompt()
    assert "4417" in first and dropped in first
    store.store("fact", "The zebra crossing project uses granite slabs.", source="user")
    store.update(secret, content="Door code is private.")
    try:
        assert "4417" not in _new_chat(store).get_memory_system_prompt()
    finally:
        store.close()


def _render_then_cap_drop(store, shown_fact=None):
    """Render a chat, then store a fact that pushes one shown fact out.

    *shown_fact* is stored last before the render, so it stays shown. Returns
    the first chat's section, the pushed-out fact's id and *shown_fact*'s id.
    """
    _thirty_facts(store)
    shown_id = shown_fact and store.store("fact", shown_fact, source="user")
    chat = _new_chat(store)
    first = chat.get_memory_system_prompt()
    shown = set(chat._stable_memory_ids)
    store.store("fact", "The zebra crossing project uses granite slabs.", source="user")
    fresh = _Host(store)
    fresh._build_stable_memory_prompt()
    dropped = sorted(shown - fresh._stable_memory_ids)
    assert dropped
    if shown_id:
        assert shown_id in fresh._stable_memory_ids and shown_fact in first
    return first, dropped[0], shown_id


def test_a_cap_drop_alone_still_reuses_the_shared_copy(store):
    first, _, _ = _render_then_cap_drop(store)
    assert _new_chat(store).get_memory_system_prompt() == first


def test_a_shown_memory_edited_during_a_cap_drop_re_renders(store):
    _, _, shown = _render_then_cap_drop(store, "The user's office is in building 7.")
    store.update(shown, content="The user's office is in building 9.")
    later = _new_chat(store).get_memory_system_prompt()
    assert "building 9" in later and "building 7" not in later


def test_a_pushed_out_memory_edited_since_is_not_shown(store):
    first, dropped, _ = _render_then_cap_drop(store)
    old = store.get_item(dropped)["content"]
    assert old in first
    store.update(dropped, content="The project list is private now.")
    assert old not in _new_chat(store).get_memory_system_prompt()


@pytest.mark.parametrize("how", ["forget", "sensitive"])
def test_a_pushed_out_memory_forgotten_or_hidden_is_not_shown(store, how):
    """Privacy: the shared copy still holds the pushed-out line; it must not leak."""
    first, dropped, _ = _render_then_cap_drop(store)
    old = store.get_item(dropped)["content"]
    assert old in first
    if how == "forget":
        store.forget(dropped)
    else:
        store.update(dropped, sensitive=True)
    assert old not in _new_chat(store).get_memory_system_prompt()


@pytest.mark.parametrize("how", ["forget", "sensitive"])
def test_a_shown_memory_forgotten_or_hidden_during_a_cap_drop_is_not_shown(store, how):
    _, _, secret = _render_then_cap_drop(store, "The user's door code is 4417.")
    if how == "forget":
        store.forget(secret)
    else:
        store.update(secret, sensitive=True)
    assert "4417" not in _new_chat(store).get_memory_system_prompt()


def test_an_untracked_profile_line_hiding_behind_a_cap_drop_re_renders(tmp_path):
    """Profile rows carry no id in the copy; their old line must not pass either."""
    probe = MemoryStore(tmp_path / "probe.db")
    probe.store("fact", "The user's team ships GPU drivers.", source="user")
    probe.store(
        "profile",
        "Lives at 12 Elm Street. placeholder",
        source="user",
        allow_privileged=True,
    )
    _thirty_facts(probe)
    dropped = _pushed_out_by_next_store(probe)
    probe.close()

    store = MemoryStore(tmp_path / "memory.db")
    try:
        store.store("fact", "The user's team ships GPU drivers.", source="user")
        profile = store.store(
            "profile",
            f"Lives at 12 Elm Street. {dropped}",
            source="user",
            allow_privileged=True,
        )
        _thirty_facts(store)
        first = _new_chat(store).get_memory_system_prompt()
        assert "Elm Street" in first and dropped in first
        store.store(
            "fact", "The zebra crossing project uses granite slabs.", source="user"
        )
        store.update(
            profile, content="Moved; address not shared.", allow_privileged=True
        )
        assert "Elm Street" not in _new_chat(store).get_memory_system_prompt()
    finally:
        store.close()
