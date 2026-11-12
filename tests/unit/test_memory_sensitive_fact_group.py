# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A sensitive fact has no plain row, and forgetting any row of it forgets it.

Round 3 of the v0.26.0-rc18 test: a word-overlap bar let wordings through.
"Allergic to mango (private health info, marked sensitive)" was stored in
plain sight beside the flagged row, and Forget on a long wording left the
short ones. Rows of one fact are now tied when they are written (by the turn
that saved them, or by what the shorter wording says), and a memory that calls
itself sensitive is always flagged. Real ``MemoryStore``, real memory tools.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from gaia.agents.base.memory_store import MemoryStore
from tests.unit.test_memory_sensitive_copy import (  # noqa: F401
    _Host,
    _rows,
    _tools,
    store,
)

LONG_MANGO = (
    "Allergic to mango — mango makes their throat swell, so no mango lassi "
    "or mango dishes."
)
SWELLS = "Allergic to mango (causes throat swelling)"
LABELLED = "Allergic to mango (private health info, marked sensitive)"
NURSE = (
    "Blood type is O negative (O−), confirmed by a nurse at a blood donation "
    "on 2026-11-12."
)

# What remember saved as sensitive, what extraction then wrote, rows left.
R3_PAIRS = [
    pytest.param(LONG_MANGO, SWELLS, 2, id="long-then-short"),
    pytest.param(SWELLS, LABELLED, 1, id="labelled-copy"),
    pytest.param(
        "Blood type is O negative (O−)",
        "Blood type is O negative (sensitive medical info)",
        1,
        id="two-flagged-blood",
    ),
    pytest.param(
        NURSE,
        "The user's blood type is O negative (sensitive medical info).",
        1,
        id="nurse-blood",
    ),
    pytest.param(
        "My salary is 92,000 euros a year.",
        "User earns 92,000 euros per year",
        2,
        id="salary",
    ),
    pytest.param(
        "I was diagnosed with type 1 diabetes in 2019.",
        "User has type 1 diabetes (diagnosed 2019)",
        2,
        id="diabetes",
    ),
]


def _flags(db: MemoryStore):
    return [(content, flag) for _, content, flag, _ in _rows(db)]


def _all_flagged(db: MemoryStore) -> bool:
    return all(flag == 1 for _, flag in _flags(db))


@pytest.mark.parametrize("saved, extracted, left", R3_PAIRS)
@pytest.mark.parametrize("extraction_first", [False, True])
@pytest.mark.parametrize("forget_saved", [True, False])
def test_the_store_alone_keeps_a_round3_pair_flagged_and_forgets_it_whole(
    store, saved, extracted, left, extraction_first, forget_saved
):
    """No turn to go by: the store's own check must hold for these wordings."""

    def save():
        return store.store(category="fact", content=saved, sensitive=True)

    def extract():
        return store.store(category="fact", content=extracted, source="llm_extract")

    first, second = (extract(), save()) if extraction_first else (save(), extract())
    saved_id, extracted_id = (second, first) if extraction_first else (first, second)

    assert _all_flagged(store), _flags(store)
    assert len(_rows(store)) == left, _flags(store)

    store.forget_with_copies(saved_id if forget_saved else extracted_id)
    assert _rows(store) == []


def _turn(host: _Host, tools, calls, extracted):
    """One chat turn: the model's memory tool calls, then the turn's extraction."""
    host._turn_sensitive_ids = []
    results = [tools[name](**args) for name, args in calls]
    host._execute_extraction_operations(
        [
            {"op": "add", "category": category, "content": content, "grounded": "user"}
            for category, content in extracted
        ],
        [],
        context="global",
        sensitive_ids=tuple(host._turn_sensitive_ids),
    )
    return results


def test_the_round3_mango_chats_leave_no_plain_row_and_one_forget_clears_them(store):
    """Stated in passing, then "remember this, it's sensitive" in a new chat."""
    host = _Host(store)
    with _tools(host) as tools:
        (kept,) = _turn(
            host,
            tools,
            [
                (
                    "remember",
                    {"fact": LONG_MANGO, "category": "preference", "sensitive": "true"},
                )
            ],
            [("fact", SWELLS)],
        )
        assert _all_flagged(store), _flags(store)

        swells = next(r[0] for r in _rows(store) if r[1] == SWELLS)
        _turn(
            host,
            tools,
            [("update_memory", {"knowledge_id": swells, "sensitive": "true"})],
            [("fact", LABELLED)],
        )

    rows = _flags(store)
    assert _all_flagged(store), rows
    assert LABELLED not in [content for content, _ in rows]

    erased = store.forget_with_copies(kept["knowledge_id"])
    assert len(erased) == len(rows)
    assert _rows(store) == []


def test_the_round3_blood_type_chats_leave_one_flagged_row(store):
    host = _Host(store)
    with _tools(host) as tools:
        _turn(host, tools, [("remember", {"fact": NURSE, "sensitive": "true"})], [])
        _turn(
            host,
            tools,
            [("remember", {"fact": "Blood type is O negative.", "sensitive": "true"})],
            [("fact", "The user's blood type is O negative (sensitive medical info).")],
        )

    assert _flags(store) == [(NURSE, 1)]


@pytest.mark.parametrize("said", ["yes", "Yes", "true", "private", "1"])
def test_any_yes_marks_a_remember_sensitive(store, said):
    """Round 3: ``sensitive: yes`` stored the blood type in plain sight."""
    host = _Host(store)
    with _tools(host) as tools:
        tools["remember"](fact="Blood type is O negative", sensitive=said)
    assert _flags(store) == [("Blood type is O negative", 1)]


@pytest.mark.parametrize("said", ["", "false", "no", "0"])
def test_a_plain_no_leaves_a_remember_unflagged(store, said):
    host = _Host(store)
    with _tools(host) as tools:
        tools["remember"](fact="User likes green tea", sensitive=said)
    assert _flags(store) == [("User likes green tea", 0)]


@pytest.mark.parametrize(
    "content",
    [
        LABELLED,
        "The user's blood type is O negative (sensitive medical info).",
        "Salary is 92,000 euros. Confidential.",
        "Takes lithium; keep it private",
        "Has a stoma (do not share)",
    ],
)
@pytest.mark.parametrize("source", ["llm_extract", "consolidation", "tool", "user"])
def test_a_memory_that_calls_itself_sensitive_is_stored_flagged(store, content, source):
    store.store(category="fact", content=content, source=source)
    assert _flags(store) == [(content, 1)]


@pytest.mark.parametrize(
    "content",
    ["User prefers case-sensitive search", "User works at a private school"],
)
def test_a_word_with_another_meaning_does_not_flag(store, content):
    store.store(category="fact", content=content, source="llm_extract")
    assert _flags(store) == [(content, 0)]


def test_editing_a_memory_to_call_itself_sensitive_flags_it(store):
    kid = store.store(category="fact", content="Allergic to mango")
    store.update(kid, content=LABELLED)
    assert _flags(store) == [(LABELLED, 1)]


def test_opening_an_older_database_flags_rows_that_call_themselves_sensitive(
    tmp_path,
):
    path = tmp_path / "old.db"
    old = MemoryStore(db_path=path)
    kid = old.store(category="fact", content="Allergic to mango")
    other = old.store(category="fact", content="User likes green tea")
    old._conn.execute("UPDATE knowledge SET content = ? WHERE id = ?", (LABELLED, kid))
    old._conn.execute("DROP INDEX idx_knowledge_fact")
    old._conn.execute("ALTER TABLE knowledge DROP COLUMN fact_id")
    old._conn.execute("UPDATE schema_version SET version = 8")
    old._conn.commit()
    old.close()

    db = MemoryStore(db_path=path)
    try:
        assert db.get_item(kid)["sensitive"] is True
        assert db.get_item(other)["sensitive"] is False
        assert db.store(category="fact", content="Likes hiking")
    finally:
        db.close()


def test_marking_one_copy_sensitive_flags_the_copies_and_one_forget_takes_all(store):
    """Copies an older build left plain are flagged with the one the user marks."""
    host = _Host(store)
    first = store.store(category="fact", content=SWELLS, source="llm_extract")
    second = store.store(
        category="preference", content=LONG_MANGO, source="llm_extract"
    )
    assert first != second

    with _tools(host) as tools:
        tools["update_memory"](knowledge_id=first, sensitive="true")

    assert _all_flagged(store), _flags(store)
    assert set(store.forget_with_copies(second)) == {first, second}


def test_unmarking_one_row_of_a_fact_unmarks_them_all(store):
    first = store.store(category="fact", content=LONG_MANGO, sensitive=True)
    second = store.store(category="fact", content=SWELLS, source="llm_extract")
    assert first != second

    store.update(second, sensitive=False)

    assert [flag for _, flag in _flags(store)] == [0, 0]


def test_a_same_turn_memory_sharing_one_word_is_flagged_but_kept_apart(store):
    """Too reworded for any wording bar: the turn flags it. It may be another
    fact (a mother's illness and where she lives), so it is not forgotten too."""
    reworded = "Mango makes the user's throat swell"
    host = _Host(store)
    with _tools(host) as tools:
        (kept,) = _turn(
            host,
            tools,
            [("remember", {"fact": "Allergic to mango", "sensitive": "true"})],
            [("fact", reworded), ("reminder", "Due for a tetanus booster")],
        )

    assert dict(_flags(store)) == {
        "Allergic to mango": 1,
        reworded: 1,
        "Due for a tetanus booster": 0,
    }

    store.forget_with_copies(kept["knowledge_id"])
    assert dict(_flags(store)) == {reworded: 1, "Due for a tetanus booster": 0}


def test_a_same_turn_fact_about_the_same_person_is_not_forgotten_with_it(store):
    cancer, austin = (
        "User's mother Elena has cancer",
        "User's mother Elena lives in Austin",
    )
    host = _Host(store)
    with _tools(host) as tools:
        (kept,) = _turn(
            host,
            tools,
            [("remember", {"fact": cancer, "sensitive": "true"})],
            [("fact", austin)],
        )

    store.forget_with_copies(kept["knowledge_id"])
    assert _flags(store) == [(austin, 1)]


def test_an_edit_that_restates_a_sensitive_fact_is_flagged(store):
    """update_memory on a plain row must not leave a plain copy either."""
    store.store(category="fact", content="User has asthma", sensitive=True)
    plain = store.store(
        category="note", content="Likes long walks", source="llm_extract"
    )

    store.update(plain, content="User has asthma, diagnosed 2020")

    assert _all_flagged(store), _flags(store)


def test_folding_a_row_keeps_its_group_with_the_fact(store):
    """The folded row's partner must still be forgotten with the fact."""
    first = store.store(category="fact", content=SWELLS, sensitive=True)
    partner = store.store(
        category="fact",
        content="Mango gives the user a swollen throat, an allergy",
        source="llm_extract",
    )
    store.link(first, partner)

    kept = store.store(
        category="fact",
        content="Allergic to mango (causes throat swelling) since childhood",
        sensitive=True,
    )

    assert kept == first
    assert set(store.forget_with_copies(kept)) == {kept, partner}


def test_a_fact_that_uses_the_word_sensitive_is_not_dropped_as_a_note(store):
    """ "sensitive to gluten" says something; "(sensitive)" does not."""
    store.store(category="fact", content="User is lactose intolerant", sensitive=True)
    store.store(
        category="fact",
        content="User is lactose intolerant, sensitive to gluten",
        source="llm_extract",
    )

    assert "gluten" in " ".join(content for content, _ in _flags(store))
    assert _all_flagged(store)


def test_unmarking_leaves_a_row_that_calls_itself_sensitive_flagged(store):
    first = store.store(category="fact", content=LONG_MANGO, sensitive=True)
    labelled = store.store(
        category="fact",
        content="Mango allergy, throat swells (marked sensitive)",
        source="llm_extract",
    )
    assert first != labelled

    store.update(first, sensitive=False)

    assert store.get_item(labelled)["sensitive"] is True
    assert store.get_item(first)["sensitive"] is True


def test_forgetting_does_not_follow_copies_of_copies(store):
    """Rows an older build left ungrouped: mango reaches "mango and peanuts"
    by wording, but must not go on from there to the peanut allergy."""
    for kid, content in [
        ("mango", "Allergic to mango"),
        ("both", "User has allergies to mango and peanuts"),
        ("peanuts", "Allergic to peanuts"),
    ]:
        store._conn.execute(
            "INSERT INTO knowledge (id, category, content, source, sensitive, "
            "created_at, updated_at) VALUES (?, 'fact', ?, 'llm_extract', 1, "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (kid, content),
        )
    store._conn.commit()

    assert set(store.forget_with_copies("mango")) == {"mango", "both"}
    assert [r[0] for r in _rows(store)] == ["peanuts"]


def test_an_extracted_correction_retires_the_old_row_even_when_covered(store):
    """The update's wording adds nothing to the sensitive row; the old row
    it corrects must still stop being current."""
    host = _Host(store)
    old = store.store(category="fact", content="Allergic to peanuts")
    with _tools(host) as tools:
        host._turn_sensitive_ids = []
        tools["remember"](fact="I am allergic to mango", sensitive="true")
        host._execute_extraction_operations(
            [
                {
                    "op": "update",
                    "knowledge_id": old,
                    "content": "Allergic to mango",
                    "grounded": "user",
                }
            ],
            [store.get_item(old)],
            context="global",
            sensitive_ids=tuple(host._turn_sensitive_ids),
        )

    current = store._conn.execute(
        "SELECT content, sensitive FROM knowledge WHERE superseded_by IS NULL"
    ).fetchall()
    assert all("peanuts" not in content for content, _ in current), current
    assert all(flag == 1 for _, flag in current), current


def test_a_same_turn_restatement_that_adds_nothing_is_not_stored(store):
    host = _Host(store)
    with _tools(host) as tools:
        _turn(
            host,
            tools,
            [("remember", {"fact": "I am allergic to mango", "sensitive": "true"})],
            [("fact", "The user has a mango allergy (health, sensitive)")],
        )
    assert _flags(store) == [("I am allergic to mango", 1)]


def test_a_same_turn_side_detail_is_flagged_but_not_forgotten_with_the_fact(store):
    """It shares the nurse and the date, not the blood type."""
    booster = "Due for a tetanus booster (nurse flagged it on 2026-11-12)"
    host = _Host(store)
    with _tools(host) as tools:
        (kept,) = _turn(
            host,
            tools,
            [("remember", {"fact": NURSE, "sensitive": "true"})],
            [("reminder", booster)],
        )

    assert dict(_flags(store)) == {NURSE: 1, booster: 1}
    store.forget_with_copies(kept["knowledge_id"])
    assert _flags(store) == [(booster, 1)]


def test_extraction_that_runs_late_still_sees_what_was_marked_meanwhile(store):
    """The turn was said before the save; its extraction ran after it."""
    host = _Host(store)
    said_at = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat()
    store.store(category="fact", content="Allergic to mango", sensitive=True)

    host._execute_extraction_operations(
        [
            {
                "op": "add",
                "category": "fact",
                "content": "Throat swells after eating mango",
                "grounded": "user",
            }
        ],
        [],
        context="global",
        said_at=said_at,
    )

    assert _all_flagged(store), _flags(store)


def test_an_extracted_rewording_of_a_sensitive_memory_stays_sensitive(store):
    """The update op stores a new row and retires the old one."""
    host = _Host(store)
    old = store.store(category="fact", content="Takes 10mg lisinopril", sensitive=True)
    host._execute_extraction_operations(
        [
            {
                "op": "update",
                "knowledge_id": old,
                "content": "Now on 20mg of ramipril every morning",
                "grounded": "user",
            }
        ],
        [store.get_item(old)],
        context="global",
    )

    current = store._conn.execute(
        "SELECT content, sensitive FROM knowledge WHERE superseded_by IS NULL"
    ).fetchall()
    assert current == [("Now on 20mg of ramipril every morning", 1)]


# Paraphrases: filler, notes, order and detail around the words that matter.
_LEADS = ["", "User ", "The user ", "I ", "My ", "The user's "]
_NOTES = [
    "",
    " (sensitive)",
    " (private health info, marked sensitive)",
    " — sensitive medical information, handle with care",
    ". Confidential.",
    " (health)",
]
_DETAIL = [
    "",
    ", confirmed by a doctor in 2021",
    " since childhood",
    " — carries an EpiPen everywhere",
    ", which the nurse noted last week",
]
_FACTS = [
    ("Allergic to mango", ["mango allergy", "allergic to mango", "allergy: mango"]),
    (
        "Blood type is O negative",
        ["blood type O negative", "O negative blood type", "negative O blood type"],
    ),
    (
        "Salary is 92,000 euros",
        ["salary of 92,000 euros", "92,000 euros salary", "euros 92,000 as salary"],
    ),
]


def _paraphrases():
    rng = random.Random(1812)
    seen = set()
    for base, cores in _FACTS:
        for _ in range(40):
            text = (
                rng.choice(_LEADS)
                + rng.choice(cores)
                + rng.choice(_DETAIL)
                + rng.choice(_NOTES)
            )
            seen.add((base, text))
    return sorted(seen)


@pytest.mark.parametrize("base, reworded", _paraphrases())
@pytest.mark.parametrize("path", ["store", "turn", "extraction-first"])
def test_no_paraphrase_is_stored_plain_and_one_forget_clears_the_fact(
    store, base, reworded, path
):
    host = _Host(store)
    with _tools(host) as tools:
        if path == "store":
            kept = store.store(category="fact", content=base, sensitive=True)
            store.store(category="note", content=reworded, source="llm_extract")
        elif path == "turn":
            (saved,) = _turn(
                host,
                tools,
                [("remember", {"fact": base, "sensitive": "true"})],
                [("note", reworded)],
            )
            kept = saved["knowledge_id"]
        else:
            store.store(category="note", content=reworded, source="llm_extract")
            kept = tools["remember"](fact=base, sensitive="true")["knowledge_id"]

    assert _all_flagged(store), _flags(store)

    store.forget_with_copies(kept)
    assert _rows(store) == []


@pytest.mark.parametrize(
    "one, other",
    [
        ("Allergic to mango", "Allergic to penicillin"),
        ("I take 10mg lisinopril daily", "I take 10mg atorvastatin daily"),
        ("My salary is 92,000 euros", "My bonus is 9,000 euros"),
        ("Blood type is O negative", "Diagnosed with asthma in 2019"),
    ],
)
def test_two_sensitive_facts_about_one_person_stay_two_facts(store, one, other):
    host = _Host(store)
    with _tools(host) as tools:
        first, second = _turn(
            host,
            tools,
            [
                ("remember", {"fact": one, "sensitive": "true"}),
                ("remember", {"fact": other, "sensitive": "true"}),
            ],
            [],
        )

    assert sorted(_flags(store)) == sorted([(one, 1), (other, 1)])
    assert second["knowledge_id"] != first["knowledge_id"]
    assert store.forget_with_copies(first["knowledge_id"]) == [first["knowledge_id"]]
    assert _flags(store) == [(other, 1)]


def test_a_plain_fact_about_someone_else_is_left_alone(store):
    host = _Host(store)
    sams = store.store(
        category="fact", content="Allergic to mango", entity="person:Sam"
    )
    with _tools(host) as tools:
        (mine,) = _turn(
            host,
            tools,
            [("remember", {"fact": "I am allergic to mango", "sensitive": "true"})],
            [],
        )
        host._execute_extraction_operations(
            [
                {
                    "op": "add",
                    "category": "fact",
                    "content": "Sam is allergic to mango too",
                    "entity": "person:Sam",
                    "grounded": "user",
                }
            ],
            [],
            context="global",
            sensitive_ids=(mine["knowledge_id"],),
        )

    store.forget_with_copies(mine["knowledge_id"])

    assert store.get_item(sams)["sensitive"] is False
    left = _flags(store)
    assert len(left) == 2 and all(flag == 0 for _, flag in left), left


def test_an_unrelated_plain_fact_about_the_user_is_left_alone(store):
    likes = store.store(category="preference", content="User likes mango lassi")
    tea = store.store(category="preference", content="User likes green tea")
    mine = store.store(category="fact", content="Allergic to mango", sensitive=True)

    assert store.get_item(likes)["sensitive"] is False
    assert store.forget_with_copies(mine) == [mine]
    assert {r[0] for r in _rows(store)} == {likes, tea}
