# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The indexing estimate: measured, honest about what it doesn't know, never frozen."""

import pytest

from gaia.ui.models import IndexingOverview, IndexProgress
from gaia.utils.index_tracker import (
    AT_LEAST,
    EMBEDDING,
    ESTIMATING,
    READING,
    READY,
    STALL_MIN_S,
    STALLED,
    WAITING,
    IndexTracker,
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def tracker(clock):
    return IndexTracker(clock=clock)


def read_pages(tracker, clock, key, pages, seconds_each=3.0, total=None, start=0):
    total = total if total is not None else pages
    tracker.report(key, READING, start, total)
    for done in range(start + 1, start + pages + 1):
        clock.tick(seconds_each)
        tracker.report(key, READING, done, total)


def embed(tracker, clock, key, chunks, seconds_each=1.0, step=4):
    tracker.report(key, EMBEDDING, 0, chunks)
    for done in range(step, chunks + 1, step):
        clock.tick(seconds_each * step)
        tracker.report(key, EMBEDDING, done, chunks)


def learn_the_machine(tracker, clock):
    """One finished document: 10 pages at 3 s, 40 chunks at 1 s."""
    tracker.enqueue("seen", "seen.pdf")
    tracker.set_pages("seen", 10)
    read_pages(tracker, clock, "seen", 10)
    embed(tracker, clock, "seen", 40)
    tracker.finish("seen", "complete")


def test_an_untouched_tracker_reads_as_empty_in_the_payload_shape(tracker):
    overview = tracker.overview()
    assert overview["batch_id"] is None
    assert overview["active"] is False
    assert overview["total"] == 0
    assert overview["percent"] is None
    assert overview["seconds_left"] is None
    assert overview["estimate"] == ESTIMATING
    assert overview["documents"] == []
    IndexingOverview(**overview)


def test_cold_start_gives_no_number(tracker, clock):
    tracker.enqueue("a", "a.pdf")
    tracker.set_pages("a", 100)
    tracker.report("a", READING, 0, 100)
    clock.tick(1)
    tracker.report("a", READING, 1, 100)

    overview = tracker.overview()
    assert overview["seconds_left"] is None
    assert overview["estimate"] == ESTIMATING
    assert tracker.progress("a")["estimate"] == ESTIMATING
    IndexProgress(**tracker.progress("a"))


def test_no_samples_while_only_queued(tracker, clock):
    tracker.enqueue("a", "a.pdf")
    tracker.set_pages("a", 100)
    clock.tick(30)

    progress = tracker.progress("a")
    assert progress["phase"] == "queued"
    assert progress["seconds_left"] is None
    assert tracker.overview()["estimate"] == ESTIMATING


def test_reading_alone_is_a_lower_bound_until_embedding_is_measured(tracker, clock):
    tracker.enqueue("a", "a.pdf")
    tracker.set_pages("a", 100)
    read_pages(tracker, clock, "a", 10, total=100)

    overview = tracker.overview()
    assert overview["estimate"] == AT_LEAST
    assert overview["seconds_left"] == pytest.approx(270, abs=2)


def test_the_estimate_includes_the_embedding_still_to_come(tracker, clock):
    learn_the_machine(tracker, clock)
    tracker.enqueue("a", "a.pdf")
    tracker.set_pages("a", 100)
    read_pages(tracker, clock, "a", 10, total=100)

    overview = tracker.overview()
    assert overview["estimate"] == READY
    # 90 pages at 3 s, then 400 chunks (4 a page) at 1 s.
    assert overview["seconds_left"] == pytest.approx(670, abs=5)


def test_phase_change_counts_real_chunks_and_not_the_reading_time(tracker, clock):
    learn_the_machine(tracker, clock)
    tracker.enqueue("a", "a.pdf")
    tracker.set_pages("a", 10)
    read_pages(tracker, clock, "a", 10)
    # A long gap between reading and embedding is not embedding time.
    clock.tick(100)
    tracker.report("a", EMBEDDING, 0, 100)
    clock.tick(8)
    tracker.report("a", EMBEDDING, 8, 100)

    assert tracker.progress("a")["phase"] == "embedding"
    assert tracker._rates[EMBEDDING].value == pytest.approx(1.0, abs=0.01)
    assert tracker._left(tracker._docs["a"])[0] == pytest.approx(92, abs=1)


def test_queued_files_join_the_estimate_and_slots_share_the_work(tracker, clock):
    learn_the_machine(tracker, clock)
    tracker.enqueue("a", "a.pdf")
    tracker.set_pages("a", 10)
    tracker.enqueue("b", "b.pdf")
    tracker.set_pages("b", 10)
    tracker.enqueue("c", "c.pdf")
    tracker.set_pages("c", 10)

    # Nothing running: one slot, every document one after another.
    assert tracker.overview()["seconds_left"] == pytest.approx(210, abs=3)

    tracker.report("a", READING, 0, 10)
    tracker.report("b", READING, 0, 10)
    overview = tracker.overview()
    assert overview["total"] == 3
    assert overview["done"] == 0
    assert overview["seconds_left"] == pytest.approx(105, abs=3)
    assert overview["current"] == "a.pdf"
    assert [d["state"] for d in overview["documents"]] == [
        "running",
        "running",
        "queued",
    ]


def test_a_big_document_is_never_promised_sooner_than_its_own_work(tracker, clock):
    learn_the_machine(tracker, clock)
    tracker.enqueue("big", "big.pdf")
    tracker.set_pages("big", 100)
    tracker.enqueue("tiny", "tiny.pdf")
    tracker.set_pages("tiny", 1)
    tracker.report("big", READING, 0, 100)
    tracker.report("tiny", READING, 0, 1)

    assert tracker.overview()["seconds_left"] >= 700


def test_several_files_stay_in_one_batch_until_all_end(tracker, clock):
    tracker.enqueue("a", "a.pdf")
    batch = tracker.overview()["batch_id"]
    tracker.enqueue("b", "b.pdf")
    tracker.finish("a", "complete")
    assert tracker.overview()["batch_id"] == batch
    tracker.enqueue("c", "c.pdf")
    assert tracker.overview()["batch_id"] == batch
    assert tracker.overview()["total"] == 3


def test_a_stall_says_so_instead_of_freezing_a_number(tracker, clock):
    learn_the_machine(tracker, clock)
    tracker.enqueue("a", "a.pdf")
    tracker.set_pages("a", 100)
    read_pages(tracker, clock, "a", 10, total=100)
    assert tracker.overview()["seconds_left"] is not None

    clock.tick(STALL_MIN_S + 1)

    overview = tracker.overview()
    assert overview["estimate"] == STALLED
    assert overview["seconds_left"] is None
    assert tracker.progress("a")["estimate"] == STALLED
    assert tracker.progress("a")["seconds_left"] is None

    tracker.report("a", READING, 11, 100)
    assert tracker.overview()["estimate"] == READY


def test_one_running_document_moving_means_the_batch_is_not_stalled(tracker, clock):
    learn_the_machine(tracker, clock)
    tracker.enqueue("a", "a.pdf")
    tracker.enqueue("b", "b.pdf")
    tracker.set_pages("a", 100)
    tracker.set_pages("b", 100)
    tracker.report("a", READING, 1, 100)
    tracker.report("b", READING, 1, 100)
    clock.tick(STALL_MIN_S - 1)
    tracker.report("b", READING, 2, 100)
    clock.tick(5)

    assert tracker.overview()["estimate"] != STALLED
    assert tracker.progress("a")["estimate"] == STALLED


def test_waiting_for_a_model_is_named_and_is_not_measured_as_slow(tracker, clock):
    learn_the_machine(tracker, clock)
    rate = tracker._rates[EMBEDDING].value
    tracker.enqueue("a", "a.pdf")
    tracker.report("a", EMBEDDING, 0, 100)
    clock.tick(STALL_MIN_S * 3)
    tracker.waiting("a")

    assert tracker.overview()["estimate"] == WAITING
    assert tracker.overview()["seconds_left"] is None

    clock.tick(4)
    tracker.report("a", EMBEDDING, 4, 100)
    assert tracker._rates[EMBEDDING].value == pytest.approx(rate, abs=0.01)


def test_zero_totals_never_divide(tracker, clock):
    tracker.enqueue("a", "a.pdf")
    tracker.set_pages("a", 0)
    tracker.report("a", READING, 0, 0)
    tracker.report("a", EMBEDDING, 0, 0)
    clock.tick(5)
    tracker.report("a", EMBEDDING, 0, 0)

    assert 0 <= tracker.overview()["percent"] <= 99
    assert tracker.progress("a")["seconds_left"] is None


def test_a_file_without_pages_is_estimated_from_whole_files(tracker, clock):
    for i in range(3):
        key = f"t{i}"
        tracker.enqueue(key, f"{key}.txt", size=1000)
        tracker.report(key, READING, 0, None)
        clock.tick(10)
        tracker.finish(key, "complete")

    tracker.enqueue("next", "next.txt", size=2000)
    assert tracker.overview()["seconds_left"] == pytest.approx(20, abs=1)
    assert tracker.overview()["estimate"] == READY


def test_the_shown_number_counts_down_and_does_not_jump(tracker, clock):
    learn_the_machine(tracker, clock)
    tracker.enqueue("a", "a.pdf")
    tracker.set_pages("a", 200)
    read_pages(tracker, clock, "a", 50, total=200)
    first = tracker.overview()["seconds_left"]

    # One slow page must not swing the estimate; steady time must count it down.
    clock.tick(40)
    tracker.report("a", READING, 51, 200)
    after_slow_page = tracker.overview()["seconds_left"]
    assert abs(after_slow_page - first) < first * 0.15

    read_pages(tracker, clock, "a", 10, total=200, start=51)
    assert tracker.overview()["seconds_left"] < after_slow_page


def test_new_work_shows_at_once(tracker, clock):
    learn_the_machine(tracker, clock)
    tracker.enqueue("a", "a.pdf")
    tracker.set_pages("a", 10)
    before = tracker.overview()["seconds_left"]
    tracker.enqueue("b", "b.pdf")
    tracker.set_pages("b", 100)
    assert tracker.overview()["seconds_left"] > before * 5


def test_percent_counts_finished_documents_and_never_slides_back(tracker, clock):
    tracker.enqueue("a", "a.pdf")
    tracker.enqueue("b", "b.pdf")
    tracker.set_pages("a", 10)
    tracker.set_pages("b", 10)
    tracker.report("a", READING, 10, 10)
    assert tracker.overview()["percent"] == 25
    tracker.report("a", EMBEDDING, 0, 40)
    assert tracker.overview()["percent"] == 25
    tracker.report("a", EMBEDDING, 40, 40)
    tracker.finish("a", "complete")
    assert tracker.overview()["percent"] == 50
    tracker.finish("b", "complete")
    assert tracker.overview()["percent"] == 100


def test_running_never_reads_as_a_full_bar(tracker):
    tracker.enqueue("a", "a.pdf")
    tracker.set_pages("a", 1)
    tracker.report("a", EMBEDDING, 10, 10)
    assert tracker.overview()["percent"] == 99


def test_failed_and_cancelled_stay_visible_after_the_batch_ends(tracker):
    tracker.enqueue("a", "a.pdf")
    tracker.enqueue("b", "b.pdf")
    tracker.enqueue("c", "c.pdf")
    tracker.finish("a", "complete")
    tracker.finish("b", "failed", "the file is damaged")
    tracker.finish("c", "cancelled")

    overview = tracker.overview()
    assert overview["active"] is False
    assert (overview["done"], overview["failed"], overview["cancelled"]) == (1, 1, 1)
    assert overview["documents"][1]["error"] == "the file is damaged"


def test_dismiss_clears_only_a_finished_batch(tracker):
    tracker.enqueue("a", "a.pdf")
    batch = tracker.overview()["batch_id"]
    tracker.dismiss(batch)
    assert tracker.overview()["total"] == 1

    tracker.finish("a", "failed", "no")
    tracker.dismiss("some-other-batch")
    assert tracker.overview()["total"] == 1
    tracker.dismiss(batch)
    assert tracker.overview()["total"] == 0


def test_a_new_batch_replaces_a_finished_one_and_keeps_the_rates(tracker, clock):
    learn_the_machine(tracker, clock)
    first = tracker.overview()["batch_id"]
    tracker.enqueue("a", "a.pdf")
    tracker.set_pages("a", 10)

    overview = tracker.overview()
    assert overview["batch_id"] != first
    assert overview["total"] == 1
    assert overview["estimate"] == READY


def test_the_first_outcome_wins_and_late_reports_are_ignored(tracker):
    tracker.enqueue("a", "a.pdf")
    tracker.finish("a", "cancelled")
    tracker.finish("a", "failed", "too late")
    tracker.report("a", READING, 5, 10)
    tracker.waiting("a")

    assert tracker.progress("a") is None
    assert tracker.overview()["cancelled"] == 1
    assert tracker.overview()["failed"] == 0


def test_key_for_finds_a_running_file_only(tracker, tmp_path):
    path = tmp_path / "a.pdf"
    path.write_text("x")
    tracker.enqueue("a", "a.pdf", str(path))
    assert tracker.key_for(str(path)) == "a"
    tracker.finish("a", "complete")
    assert tracker.key_for(str(path)) is None


def test_adding_a_running_document_again_changes_nothing(tracker):
    tracker.enqueue("a", "a.pdf", "/docs/a.pdf")
    tracker.report("a", READING, 3, 10)
    tracker.enqueue("a", "temp-copy.pdf", "/tmp/gaia_upload_x.pdf")

    assert tracker.progress("a")["done"] == 3
    assert tracker.overview()["documents"][0]["filename"] == "a.pdf"
