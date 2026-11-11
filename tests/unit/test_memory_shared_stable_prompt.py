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
