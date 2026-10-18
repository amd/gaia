# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Stored memories relevant to the current request must reach the model.

The stable memory prompt is frozen at session start and picks facts by
confidence alone, with no regard to what is being asked. Everything else was
reachable only through an explicit ``recall`` call, which models rarely make: 4
calls in a 2,121-task benchmark sweep.

Each turn now surfaces the few memories most relevant to the request in the
per-turn dynamic context, which is prepended to the user message so the cached
system prompt is unchanged. Every surfaced memory, there and in the stable
prompt, carries the absolute dates it was learned and last confirmed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict

import numpy as np
import pytest

from gaia.agents.base import memory as memory_module
from gaia.agents.base.memory import (
    LESSON_DOMAIN,
    LESSON_SOURCE,
    MemoryMixin,
    _embedding_to_blob,
    _names_in,
)
from gaia.agents.base.memory_store import MemoryStore

DIM = 8


def _axis(i: int, lean: float = 0.0, towards: int = 7) -> np.ndarray:
    """Unit vector on axis *i*, optionally leaning towards another axis."""
    vec = np.zeros(DIM, dtype=np.float32)
    vec[i] = 1.0
    vec[towards] += lean
    return vec / np.linalg.norm(vec)


class _Host(MemoryMixin):
    def __init__(self, store: MemoryStore, vectors: Dict[str, np.ndarray]):
        self._memory_store = store
        self._memory_context = "work"
        self._embedding_dim = DIM
        self._vectors = vectors
        self._faiss_index = None
        self._faiss_id_map = []

    def _embed_text(self, text):
        return self._vectors[text]


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


def _remember(store, vectors, content, vec, **kwargs):
    kwargs.setdefault("context", "work")
    kwargs.setdefault("category", "fact")
    kid = store.store(content=content, **kwargs)
    store.store_embedding(kid, _embedding_to_blob(vec))
    vectors[content] = vec
    return kid


def _backdate(store, kid, created, updated):
    with store._lock:
        store._conn.execute(
            "UPDATE knowledge SET created_at = ?, updated_at = ? WHERE id = ?",
            (created, updated, kid),
        )
        store._conn.commit()


QUERY = "why does the toybox test suite fail on CI"


@pytest.fixture
def host(store):
    vectors: Dict[str, np.ndarray] = {QUERY: _axis(0)}
    h = _Host(store, vectors)
    h.vectors = vectors
    return h


class TestRelevantMemoriesAreSurfaced:
    def test_the_relevant_note_reaches_the_turn_with_its_age(self, host, store):
        kid = _remember(
            store,
            host.vectors,
            "toybox tests need TOYBOX_CLOCK=frozen or they fail",
            _axis(0, lean=0.3),
            category="note",
        )
        _backdate(store, kid, "2026-06-03T10:00:00+00:00", "2026-09-20T08:00:00+00:00")
        _remember(
            store,
            host.vectors,
            "User's cat is called Miso",
            _axis(3),
            category="note",
        )
        host._rebuild_faiss_index()
        host._memory_turn_query = QUERY

        ctx = host.get_memory_dynamic_context()

        assert "Stored memories that may bear on this message:" in ctx
        assert (
            "  - [note] toybox tests need TOYBOX_CLOCK=frozen or they fail "
            "(learned 2026-06-03, last confirmed 2026-09-20)"
        ) in ctx
        assert "Miso" not in ctx

    def test_only_the_top_few_are_surfaced(self, host, store):
        topics = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
        for i, topic in enumerate(topics):
            _remember(
                store,
                host.vectors,
                f"{topic} fact",
                _axis(0, lean=0.1 * (i + 1)),
                category="fact",
            )
        host._rebuild_faiss_index()
        host._memory_turn_query = QUERY

        ctx = host.get_memory_dynamic_context()

        assert ctx.count(" fact (learned ") == 3
        assert "alpha fact" in ctx and "foxtrot fact" not in ctx

    def test_what_the_stable_prompt_shows_is_not_repeated(self, host, store):
        _remember(
            store,
            host.vectors,
            "toybox CI runs on Python 3.12",
            _axis(0, lean=0.2),
            category="fact",
            confidence=0.9,
        )
        host._rebuild_faiss_index()
        host._memory_turn_query = QUERY

        stable = host.get_memory_system_prompt()
        ctx = host.get_memory_dynamic_context()

        assert "toybox CI runs on Python 3.12" in stable
        assert "toybox CI runs on Python 3.12" not in ctx

    def test_surfacing_does_not_count_as_a_use(self, host, store):
        kid = _remember(
            store,
            host.vectors,
            "toybox CI needs a frozen clock",
            _axis(0),
            category="note",
            confidence=0.4,
        )
        host._rebuild_faiss_index()
        host._memory_turn_query = QUERY

        host.get_memory_dynamic_context()

        item = store.get_item(kid)
        assert item["confidence"] == pytest.approx(0.4)
        assert item["use_count"] == 0


class TestContextScope:
    def test_a_scoped_session_skips_other_contexts(self, host, store):
        _remember(
            store,
            host.vectors,
            "toybox CI personal-context row",
            _axis(0),
            category="note",
            context="personal",
        )
        host._rebuild_faiss_index()
        host._memory_turn_query = QUERY

        assert "personal-context row" not in host.get_memory_dynamic_context()

    def test_a_default_session_reads_every_context(self, host, store):
        _remember(
            store,
            host.vectors,
            "toybox CI personal-context row",
            _axis(0),
            category="note",
            context="personal",
        )
        host._rebuild_faiss_index()
        host._memory_context = "global"
        host._memory_turn_query = QUERY

        assert "personal-context row" in host.get_memory_dynamic_context()


class TestWhatIsNeverSurfaced:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"category": "note", "sensitive": True},
            {"category": "reminder", "due_at": "2030-01-01T00:00:00+00:00"},
        ],
        ids=["sensitive", "reminder"],
    )
    def test_excluded_rows(self, host, store, kwargs):
        _remember(store, host.vectors, "toybox CI secret-ish row", _axis(0), **kwargs)
        host._rebuild_faiss_index()
        host._memory_turn_query = QUERY

        assert "toybox CI secret-ish row" not in host.get_memory_dynamic_context()

    def test_a_lesson_from_another_workspace_stays_out(self, host, store):
        """A lesson is filed as a note, but belongs to one workspace.

        Per-turn recall is unscoped in a default ``global`` session, so without
        a domain check a lesson learned in project A surfaces in project B —
        and it lands as a bare note, without the anti-injection framing its own
        path wraps it in, even though its content is raw tool output.
        """
        _remember(
            store,
            host.vectors,
            "toybox CI needs --no-sandbox",
            _axis(0),
            category="note",
            domain=LESSON_DOMAIN,
            source=LESSON_SOURCE,
            context="workspace:/some/other/project",
        )
        host._rebuild_faiss_index()
        host._memory_context = "global"
        host._memory_turn_query = QUERY

        assert "--no-sandbox" not in host.get_memory_dynamic_context()

    def test_a_fact_the_model_labelled_lesson_still_surfaces(self, host, store):
        """``domain`` is the extraction model's word; ``source`` is the code's.

        A turn that merely mentions a lesson gets its ordinary facts tagged
        ``domain='lesson'`` by the model. Keying the exclusion on that would
        suppress a real fact from recall permanently.
        """
        _remember(
            store,
            host.vectors,
            "toybox CI runs on the nightly image",
            _axis(0),
            category="fact",
            domain=LESSON_DOMAIN,
            source="llm_extract",
        )
        host._rebuild_faiss_index()
        host._memory_turn_query = QUERY

        assert "nightly image" in host.get_memory_dynamic_context()

    def test_an_unrelated_nearest_neighbour_is_below_the_floor(self, host, store):
        _remember(
            store,
            host.vectors,
            "User prefers tabs over spaces",
            _axis(0, lean=2.0, towards=5),
            category="note",
        )
        host._rebuild_faiss_index()
        host._memory_turn_query = QUERY

        assert "tabs over spaces" not in host.get_memory_dynamic_context()

    def test_a_credential_is_masked(self, host, store):
        _remember(
            store,
            host.vectors,
            "toybox CI deploy password is hunter2hunter2",
            _axis(0),
            category="note",
        )
        host._rebuild_faiss_index()
        host._memory_turn_query = QUERY

        ctx = host.get_memory_dynamic_context()

        assert "hunter2" not in ctx
        assert "[redacted: this memory looks like a credential." in ctx

    def test_an_embedding_failure_still_returns_the_time(self, host, store):
        _remember(store, host.vectors, "toybox CI note", _axis(0), category="note")
        host._rebuild_faiss_index()
        host._memory_turn_query = "a query the embedder cannot handle"

        ctx = host.get_memory_dynamic_context()

        assert ctx.startswith("[GAIA Memory Context]\nCurrent time: ")
        assert "Stored memories" not in ctx


class TestTruncatedItemsStayRecallable:
    """A memory the 4000-char cap left out is missing from the stable prompt.

    It must not also be suppressed from per-turn recall, or it becomes
    invisible on both paths -- worse than before the ages were added.
    """

    TARGET = "toybox CI fails without TOYBOX_CLOCK=frozen " + " ".join(
        f"tail{n}" for n in range(60)
    )

    @classmethod
    def _saturate(cls, host, store):
        """Fill the stable prompt past its cap; return the left-out error memory.

        Every line needs its own vocabulary -- ``MemoryStore.store`` dedupes on
        >80% word overlap, so repeated filler would collapse into one row.
        """
        for i in range(10):
            words = " ".join(f"w{i}x{n}" for n in range(60))
            _remember(
                store,
                host.vectors,
                f"Error {i}: {words}",
                _axis(4 + (i % 3)),
                category="error",
                confidence=0.9,
            )
        target = _remember(
            store,
            host.vectors,
            cls.TARGET,
            _axis(0, lean=0.3),
            category="error",
            confidence=0.1,
        )
        # The oldest, so the newer rows fill the budget first.
        _backdate(
            store, target, "2026-01-01T10:00:00+00:00", "2026-01-01T10:00:00+00:00"
        )
        return target

    def test_a_left_out_memory_is_still_offered_to_the_turn(self, host, store):
        self._saturate(host, store)
        host._rebuild_faiss_index()
        host._memory_turn_query = QUERY

        stable = host.get_memory_system_prompt()
        ctx = host.get_memory_dynamic_context()

        assert "more stored memories are not listed here" in stable
        assert "TOYBOX_CLOCK=frozen" not in stable
        assert self.TARGET in ctx

    def test_only_surviving_lines_are_suppressed(self, host, store):
        cut = self._saturate(host, store)

        host.get_memory_system_prompt()

        assert cut not in host._stable_memory_ids
        assert host._stable_memory_ids, "items that survived the cap must be recorded"


class TestStablePromptAges:
    def test_each_personal_item_carries_its_dates(self, host, store):
        fact = store.store(
            category="fact", content="Team ships on Fridays", confidence=0.8
        )
        pref = store.store(category="preference", content="Prefers short answers")
        _backdate(store, fact, "2026-06-03T10:00:00+00:00", "2026-09-20T08:00:00+00:00")
        _backdate(store, pref, "2026-07-01T10:00:00+00:00", "2026-07-01T10:00:00+00:00")

        prompt = host.get_memory_system_prompt()

        assert (
            "  - Team ships on Fridays (learned 2026-06-03, last confirmed 2026-09-20)"
        ) in prompt
        assert "  - Prefers short answers (learned 2026-07-01)" in prompt
        assert "confidence" not in prompt


class _AgentBase:
    def process_query(self, user_input, **kwargs):
        self.sent = user_input
        return {"result": ""}


class _TurnHost(_Host, _AgentBase):
    pass


def test_the_memory_rides_on_the_user_message_not_the_system_prompt(store):
    vectors: Dict[str, np.ndarray] = {QUERY: _axis(0)}
    host = _TurnHost(store, vectors)
    system_before = host.get_memory_system_prompt()
    # Learned mid-session, after the stable prompt froze.
    _remember(
        store, vectors, "toybox CI needs a frozen clock", _axis(0), category="note"
    )
    host._rebuild_faiss_index()

    host.process_query(QUERY)

    before_question, _, after = host.sent.partition(QUERY)
    assert "  - [note] toybox CI needs a frozen clock" in before_question
    assert after == ""
    assert host.get_memory_system_prompt() == system_before
    assert "frozen clock" not in system_before


# ---------------------------------------------------------------------------
# Calibration for a small real embedder. With embeddinggemma-300m a natural
# question scores the memory it is about at 0.35-0.55 and unrelated memories at
# 0.25-0.40 ("What should I get my sister for her birthday?" vs "My sister
# Priya lives in Vancouver": 0.47). A fixed 0.5 floor surfaced nothing.
# ---------------------------------------------------------------------------


def _at(cos: float, axis: int = 1) -> np.ndarray:
    """Unit vector whose cosine with the query (axis 0) is exactly *cos*."""
    vec = np.zeros(DIM, dtype=np.float32)
    vec[0] = cos
    vec[axis] = np.sqrt(1.0 - cos * cos)
    return vec


_NOISE = [
    "The team standup is at half past nine",
    "Laptop is a ThinkPad with thirty two gigabytes",
    "Uses an editor with modal keybindings",
    "Office moved up to the fifth floor",
    "Runs five kilometres on weekday evenings",
    "Favourite band plays alternative rock",
    "Practises guitar scales before breakfast",
    "Production database runs version sixteen",
    "Takes the bus into work most days",
    "Prefers dark mode in every application",
]


def _seed_field(store, vectors, cos: float, count: int = 8) -> None:
    for i, content in enumerate(_NOISE[:count]):
        _remember(store, vectors, content, _at(cos, 1 + i % 7), category="fact")


def _surfaced(host) -> str:
    host._rebuild_faiss_index()
    host._memory_turn_query = QUERY
    return host.get_memory_dynamic_context()


class TestRecallForNaturalQuestions:
    def test_a_match_that_stands_out_from_the_field_is_surfaced(self, host, store):
        _seed_field(store, host.vectors, 0.30)
        _remember(
            store,
            host.vectors,
            "My sister Priya lives in Vancouver",
            _at(0.46),
            category="fact",
        )

        assert "Priya lives in Vancouver" in _surfaced(host)

    def test_a_best_match_at_noise_level_is_not(self, host, store):
        _seed_field(store, host.vectors, 0.30)
        _remember(store, host.vectors, "My sister Priya lives in Vancouver", _at(0.36))

        assert "Stored memories" not in _surfaced(host)

    def test_a_match_that_does_not_clear_a_high_field_is_not(self, host, store):
        _seed_field(store, host.vectors, 0.42)
        _remember(store, host.vectors, "My sister Priya lives in Vancouver", _at(0.46))

        assert "Stored memories" not in _surfaced(host)

    def test_a_match_far_behind_the_best_is_not(self, host, store):
        _seed_field(store, host.vectors, 0.30)
        _remember(store, host.vectors, "I drive a 2019 Subaru Outback", _at(0.48))
        _remember(store, host.vectors, "I prefer metric units", _at(0.40, 2))

        ctx = _surfaced(host)

        assert "Subaru Outback" in ctx
        assert "metric units" not in ctx

    def test_a_small_store_keeps_the_sure_floor(self, host, store):
        """Too few memories for a field; they all fit in the stable prompt."""
        _seed_field(store, host.vectors, 0.30, count=3)
        _remember(store, host.vectors, "My sister Priya lives in Vancouver", _at(0.46))

        assert "Stored memories" not in _surfaced(host)


class TestNamedPeopleComeAlong:
    def test_a_memory_naming_someone_a_match_names_comes_along(self, host, store):
        _seed_field(store, host.vectors, 0.25)
        _remember(
            store, host.vectors, "I have two children named Ada and Leo", _at(0.44)
        )
        _remember(store, host.vectors, "Leo is allergic to peanuts", _at(0.36, 2))
        _remember(store, host.vectors, "Snow fell early this year", _at(0.36, 3))

        ctx = _surfaced(host)

        assert "children named Ada and Leo" in ctx
        assert "Leo is allergic to peanuts" in ctx
        assert "Snow fell" not in ctx

    def test_it_may_go_past_the_top_few(self, host, store):
        _seed_field(store, host.vectors, 0.25)
        _remember(
            store, host.vectors, "I have two children named Ada and Leo", _at(0.52)
        )
        _remember(store, host.vectors, "Packs lunches the night before", _at(0.51, 2))
        _remember(store, host.vectors, "Shops for groceries on Sundays", _at(0.50, 3))
        _remember(store, host.vectors, "Kids lunchbox must be nut free", _at(0.49, 4))
        _remember(store, host.vectors, "Leo's school has a nut ban", _at(0.36, 5))

        ctx = _surfaced(host)

        assert "Leo's school has a nut ban" in ctx
        assert "Kids lunchbox must be nut free" not in ctx

    def test_names_are_capitalised_words_that_do_not_open_a_sentence(self):
        assert _names_in("I have two children named Ada and Leo.") == {"Ada", "Leo"}
        assert _names_in("My partner's name is Sam. Sam's car is red") == {"Sam"}
        assert _names_in("Leo is allergic to peanuts") == set()


class TestStablePromptFillsItsBudget:
    def test_more_than_the_old_per_category_cap_is_shown(self, host, store):
        facts = [f"Fact number {word} about the user" for word in _NOISE]
        for content in facts:
            store.store(category="fact", content=content, context="work")

        prompt = host.get_memory_system_prompt()

        assert all(content in prompt for content in facts)
        assert "not listed here" not in prompt

    def test_one_long_category_does_not_crowd_out_the_others(self, host, store):
        for i in range(30):
            words = " ".join(f"w{i}x{n}" for n in range(30))
            store.store(
                category="preference",
                content=f"Preference {i}: {words}",
                context="work",
                confidence=0.9,
            )
        fact = store.store(
            category="fact", content="Lives in Calgary", context="work", confidence=0.4
        )

        prompt = host.get_memory_system_prompt()

        assert "  - Lives in Calgary (" in prompt
        assert fact in host._stable_memory_ids
        assert "more stored memories are not listed here" in prompt
        assert len(prompt) <= 4000

    def test_notes_and_upcoming_reminders_are_shown_lessons_are_not(self, host, store):
        store.store(
            category="note", content="Book club meets Thursdays", context="work"
        )
        store.store(
            category="note",
            content="toybox build needs --no-sandbox",
            domain=LESSON_DOMAIN,
            source=LESSON_SOURCE,
            context="work",
        )
        store.store(
            category="reminder",
            content="Renew the passport",
            due_at="2030-01-01T12:00:00+00:00",
            context="work",
        )
        store.store(
            category="reminder",
            content="Dentist cleaning appointment",
            due_at="2020-01-01T00:00:00+00:00",
            context="work",
        )

        prompt = host.get_memory_system_prompt()

        assert "Notes:\n  - Book club meets Thursdays (" in prompt
        assert "--no-sandbox" not in prompt.split("Notes:")[1]
        assert "  - Renew the passport (due 2030-01-01, learned " in prompt
        assert "Dentist" not in prompt, "a reminder whose day has passed stays out"

    def test_the_most_recently_confirmed_fact_comes_first(self, host, store):
        """An old high-confidence fact must not outrank the one that replaced it."""
        old = store.store(
            category="fact", content="Lives in Calgary", context="work", confidence=0.9
        )
        new = store.store(
            category="fact", content="Moved to Toronto", context="work", confidence=0.4
        )
        _backdate(store, old, "2026-01-01T10:00:00+00:00", "2026-01-01T10:00:00+00:00")
        _backdate(store, new, "2026-09-01T10:00:00+00:00", "2026-09-01T10:00:00+00:00")

        prompt = host.get_memory_system_prompt()

        assert prompt.index("Moved to Toronto") < prompt.index("Lives in Calgary")


def test_saving_stays_silent(host):
    """Memory works in the background: the model doesn't announce saves."""
    prompt = host.get_memory_system_prompt()

    assert "call the tool silently and respond naturally" in prompt


class TestStablePromptOrderAndDueDates:
    def test_newest_first_holds_past_the_fetch_limit(self, host, store, monkeypatch):
        """The store is read newest first, not confidence-first then re-sorted."""
        monkeypatch.setattr(memory_module, "STABLE_MEMORY_FETCH_LIMIT", 3)
        for i, word in enumerate(["alpha", "bravo", "charlie"]):
            kid = store.store(
                category="fact",
                content=f"Old {word} fact",
                context="work",
                confidence=0.9,
            )
            _backdate(
                store,
                kid,
                f"2026-01-0{i + 1}T10:00:00+00:00",
                "2026-01-05T10:00:00+00:00",
            )
        store.store(
            category="fact", content="Moved to Toronto", context="work", confidence=0.4
        )

        assert "Moved to Toronto" in host.get_memory_system_prompt()

    def test_a_due_date_is_read_as_a_local_day(self, host, store):
        due = datetime.now().astimezone().replace(hour=23, minute=0, microsecond=0)
        store.store(
            category="reminder",
            content="Call the plumber",
            due_at=due.astimezone(timezone.utc).isoformat(),
            context="work",
        )

        prompt = host.get_memory_system_prompt()

        assert f"  - Call the plumber (due {due.date().isoformat()}, " in prompt

    def test_a_malformed_due_date_names_the_memory(self, host, store):
        kid = store.store(category="reminder", content="Renew it", context="work")
        with store._lock:
            store._conn.execute(
                "UPDATE knowledge SET due_at = 'next tuesday' WHERE id = ?", (kid,)
            )
            store._conn.commit()

        with pytest.raises(ValueError, match=kid):
            host.get_memory_system_prompt()

    def test_session_summaries_stay_out_of_the_always_on_section(self, host, store):
        """A summary is dated history; always on, it would contradict newer facts."""
        store.store(
            category="note",
            content="In March the user lived in Calgary and worked at Northwind",
            source="consolidation",
            domain="session:abcd1234",
            context="work",
        )
        store.store(
            category="fact",
            content="User's dog is called Juniper",
            source="consolidation",
            context="work",
        )

        prompt = host.get_memory_system_prompt()

        assert "lived in Calgary" not in prompt
        assert "dog is called Juniper" in prompt
