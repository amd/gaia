# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""store() dedup must merge only true duplicates, never a different fact (#4732)."""

import pytest

from gaia.agents.base.memory_store import MemoryStore


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(db_path=tmp_path / "memory.db")
    yield s
    s.close()


def _active_contents(store):
    rows = store._conn.execute(
        "SELECT content FROM knowledge WHERE superseded_by IS NULL"
    ).fetchall()
    return sorted(r[0] for r in rows)


# Pairs that share most of their words but state different facts. Storing the
# second must leave the first intact, in either order.
DISTINCT_PAIRS = [
    ("My mother's name is Elena.", "My mother-in-law's name is Miriam."),
    ("My mother's name is Elena.", "My mother-in-law's name is Elena."),
    ("My sister's name is Ana Lopez.", "My brother's name is Ana Lopez."),
    ("User is vegetarian at home", "User is not vegetarian at home"),
    (
        "Project uses React 18 with webpack bundler",
        "Project uses React 19 with webpack bundler",
    ),
    ("Ana likes Bob from the office", "Bob likes Ana from the office"),
    (
        "Meeting with Sarah is on Monday at 10am",
        "Meeting with Sarah is on Tuesday at 10am",
    ),
    (
        "User lives in Austin Texas near the lake",
        "User lives in Dallas Texas near the lake",
    ),
    ("Dog is named Rex and is brown", "Dog is named Max and is brown"),
]


@pytest.mark.parametrize("first,second", DISTINCT_PAIRS)
@pytest.mark.parametrize("reverse", [False, True])
def test_near_miss_keeps_both_facts(store, first, second, reverse):
    if reverse:
        first, second = second, first
    id1 = store.store(category="fact", content=first)
    id2 = store.store(category="fact", content=second)

    assert id1 != id2
    assert _active_contents(store) == sorted([first, second])


def test_issue_pair_survives_with_entity_scope(store):
    id1 = store.store(
        category="fact", content="My mother's name is Elena.", entity="person:user"
    )
    store.store(
        category="fact",
        content="My mother-in-law's name is Miriam.",
        entity="person:user",
    )
    assert store.get_item(id1)["content"] == "My mother's name is Elena."


@pytest.mark.parametrize(
    "first,second",
    [
        ("GAIA supports NPU acceleration", "GAIA supports NPU acceleration"),
        ("GAIA supports NPU acceleration", "gaia supports npu acceleration!"),
        ("User prefers dark mode", "The user prefers dark mode."),
        ("User's favorite color is blue", "My favorite color is blue"),
    ],
)
def test_restatement_still_dedups(store, first, second):
    id1 = store.store(category="fact", content=first)
    id2 = store.store(category="fact", content=second)

    assert id2 == id1
    assert len(_active_contents(store)) == 1


def test_refinement_replaces_with_fuller_content(store):
    id1 = store.store(category="fact", content="My mother's name is Elena")
    id2 = store.store(category="fact", content="My mother's name is Elena Petrova")

    assert id2 == id1
    assert _active_contents(store) == ["My mother's name is Elena Petrova"]


def test_shorter_restatement_keeps_fuller_content(store):
    id1 = store.store(category="fact", content="My mother's name is Elena Petrova")
    id2 = store.store(category="fact", content="My mother's name is Elena")

    assert id2 == id1
    assert _active_contents(store) == ["My mother's name is Elena Petrova"]


@pytest.mark.parametrize(
    "first,second",
    [
        ("Standup meeting is at 10", "Standup meeting is at 10:30"),
        ("GAIA release is version 2", "GAIA release is version 2.1"),
        ("Monthly rent is 1,200", "Monthly rent is 1,200,000"),
    ],
)
def test_a_longer_number_is_not_a_refinement(store, first, second):
    store.store(category="fact", content=first)
    store.store(category="fact", content=second)
    assert _active_contents(store) == sorted([first, second])


def test_numbers_are_never_filler(store):
    store.store(category="fact", content="Ada is 6 years old and loves math")
    store.store(category="fact", content="Ada is 7 years old and loves math")
    assert len(_active_contents(store)) == 2


def test_extraction_update_supersedes_instead_of_overwriting(store):
    """A value change goes through supersession, so the old row stays on record."""
    old_id = store.store(category="fact", content="Project uses React 18 with webpack")
    new_id = store.store(category="fact", content="Project uses React 19 with webpack")
    assert new_id != old_id

    store.update(old_id, superseded_by=new_id)
    assert store.get_item(old_id)["content"] == "Project uses React 18 with webpack"
    assert _active_contents(store) == ["Project uses React 19 with webpack"]
