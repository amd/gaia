"""The /memory read-only snapshot: what the TUI's memory view actually sees.

build_memory_dump() is tested against a REAL MemoryStore (temp-dir SQLite),
not a mock — a mock that returns a hardcoded list only proves the function
was called, not that the store's real query/dedup/schema behavior produced
the rows a user would actually see (CLAUDE.md: mocks prove "we called it",
not "the call is valid").
"""

import json

import pytest
from gaia_agent import stdio
from gaia_agent.memory_dump import (
    MAX_MEMORY_DUMP_ITEMS,
    MEMORY_DUMP_QUERY,
    build_memory_dump,
)

from gaia.agents.base.memory_store import VALID_CATEGORIES, MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memory.db")


class _FakeAgent:
    """A MemoryMixin-shaped double: only the two attributes build_memory_dump reads."""

    def __init__(self, memory_store=None, unavailable_message=None):
        self.memory_store = memory_store
        self._unavailable_message = unavailable_message

    def memory_unavailable_message(self):
        return self._unavailable_message


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def test_reports_unavailable_with_the_real_reason_not_an_empty_list():
    """A None store must not read as 'zero memories' — that's a different fact."""
    agent = _FakeAgent(
        memory_store=None,
        unavailable_message="Memory is unavailable this session: Lemonade is not reachable.",
    )

    dump = build_memory_dump(agent)

    assert dump["available"] is False
    assert "Lemonade is not reachable" in dump["reason"]
    assert "items" not in dump


def test_unavailable_falls_back_to_a_generic_reason_if_none_given():
    agent = _FakeAgent(memory_store=None, unavailable_message=None)

    dump = build_memory_dump(agent)

    assert dump["available"] is False
    assert dump["reason"]  # never blank — an empty reason is as useless as none


# ---------------------------------------------------------------------------
# Real rows from a real store
# ---------------------------------------------------------------------------


def test_one_row_per_category_round_trips(store):
    for category in sorted(VALID_CATEGORIES):
        store.store(
            category=category,
            content=f"a {category} entry about the user",
            context="global",
            confidence=0.6,
            source="user",
        )

    dump = build_memory_dump(_FakeAgent(memory_store=store))

    assert dump["available"] is True
    seen_categories = {item["category"] for item in dump["items"]}
    assert seen_categories == VALID_CATEGORIES
    assert dump["stats"]["total_knowledge"] == len(VALID_CATEGORIES)
    for category in VALID_CATEGORIES:
        assert dump["stats"]["by_category"][category] == 1


def test_item_fields_the_tui_view_needs_are_present(store):
    store.store(
        category="preference",
        content="prefers dark mode",
        context="work",
        confidence=0.77,
        entity="person:kalin",
        source="user",
    )

    dump = build_memory_dump(_FakeAgent(memory_store=store))
    item = dump["items"][0]

    assert item["content"] == "prefers dark mode"
    assert item["category"] == "preference"
    assert item["context"] == "work"
    assert item["entity"] == "person:kalin"
    assert item["confidence"] == pytest.approx(0.77)
    assert item["sensitive"] is False
    assert item["created_at"]
    assert item["updated_at"]


def test_sensitive_rows_are_shown_not_filtered(store):
    """The whole point is observability — a hidden plaintext secret defeats it."""
    store.store(
        category="fact",
        content="wifi password is hunter2",
        context="global",
        sensitive=True,
        source="user",
    )

    dump = build_memory_dump(_FakeAgent(memory_store=store))

    assert dump["stats"]["sensitive_count"] == 1
    assert any(item["sensitive"] for item in dump["items"])
    assert any("hunter2" in item["content"] for item in dump["items"])


def test_shown_vs_total_reports_truncation_honestly(store):
    for i in range(MAX_MEMORY_DUMP_ITEMS + 5):
        store.store(
            category="note",
            content=f"distinct note number {i} with unique wording token{i}",
            context="global",
            source="user",
        )

    dump = build_memory_dump(_FakeAgent(memory_store=store))

    assert dump["total"] == MAX_MEMORY_DUMP_ITEMS + 5
    assert dump["shown"] == MAX_MEMORY_DUMP_ITEMS
    assert len(dump["items"]) == MAX_MEMORY_DUMP_ITEMS


def test_contexts_are_reported(store):
    store.store(category="fact", content="fact one", context="global", source="user")
    store.store(category="fact", content="fact two", context="work", source="user")

    dump = build_memory_dump(_FakeAgent(memory_store=store))

    contexts = {c["context"]: c["count"] for c in dump["contexts"]}
    assert contexts["global"] == 1
    assert contexts["work"] == 1


def test_empty_store_is_available_with_zero_items(store):
    dump = build_memory_dump(_FakeAgent(memory_store=store))

    assert dump["available"] is True
    assert dump["items"] == []
    assert dump["total"] == 0


# ---------------------------------------------------------------------------
# stdio dispatch: the sentinel never reaches the LLM, never becomes a turn
# ---------------------------------------------------------------------------


class _RecordingAgent:
    """process_query() would prove the sentinel leaked to the LLM if called."""

    def __init__(self, memory_store=None):
        self.memory_store = memory_store
        self.process_query_calls = []
        self.conversation_history = []

    def memory_unavailable_message(self):
        return None

    def process_query(self, query):
        self.process_query_calls.append(query)
        raise AssertionError("the memory-dump sentinel must never reach process_query")


def test_dispatch_routes_the_sentinel_away_from_the_llm(store):
    import io

    agent = _RecordingAgent(memory_store=store)
    out = io.StringIO()

    stdio.dispatch_query(agent, MEMORY_DUMP_QUERY, out)

    assert agent.process_query_calls == []
    assert agent.conversation_history == []  # not recorded as a chat turn

    lines = [line for line in out.getvalue().split("\n") if line.strip()]
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["type"] == "final"
    payload = json.loads(event["answer"])
    assert payload["available"] is True


def test_dispatch_reports_unavailable_through_the_same_event_shape():
    import io

    agent = _RecordingAgent(memory_store=None)
    out = io.StringIO()

    stdio.dispatch_query(agent, MEMORY_DUMP_QUERY, out)

    event = json.loads(out.getvalue().strip())
    payload = json.loads(event["answer"])
    assert payload["available"] is False
    assert payload["reason"]


def test_dispatch_sends_a_normal_query_to_run_turn(store):
    """A real question still goes through the ordinary turn machinery."""
    import io

    class _AnsweringAgent(_RecordingAgent):
        def process_query(self, query):
            self.process_query_calls.append(query)
            return {"answer": f"answered: {query}"}

    agent = _AnsweringAgent(memory_store=store)
    agent.console = None
    agent.loaded_skills = {}
    out = io.StringIO()

    stdio.dispatch_query(agent, "what do you remember about me?", out)

    assert agent.process_query_calls == ["what do you remember about me?"]
